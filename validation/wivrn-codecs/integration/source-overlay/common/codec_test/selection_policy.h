// SPDX-License-Identifier: MIT
#pragma once
#include "selection_trace.h"
#include <algorithm>
#include <limits>

namespace wivrn::codec_test::selection_policy {
using selection_trace::Frame;
using selection_trace::Policy;
using selection_trace::Reason;
using Pair = std::array<Frame, selection_trace::decoder_count>;
using History = std::array<Frame, selection_trace::decoder_count * selection_trace::history_slots>;
using Slots = std::array<int, selection_trace::decoder_count>;
inline constexpr Slots absent{-1, -1, -1};

// This is an encoded-frame scheduling experiment. Encoded frame IDs and source
// prediction targets do not identify application generations or photon times.
inline int64_t period_bound(int64_t target, int64_t period, int64_t previous_target) {
    if (period <= 0 || previous_target <= 0 || target <= previous_target)
        return 0;
    return std::min(period, target - previous_target);
}

inline bool matches(const Frame& left, const Frame& right, bool strict) {
    return left.present && right.present && left.frame == right.frame && left.warp == right.warp &&
           (!strict || (left.view_display_time == right.view_display_time && left.alpha == right.alpha));
}

inline Slots pair_slots(const History& history, int left_slot, bool alpha, bool strict) {
    const auto& left = history[left_slot];
    if (!left.present || (strict && (left.alpha != alpha || left.view_display_time <= 0)))
        return absent;
    Slots slots{left_slot, -1, -1};
    for (std::size_t decoder = 1; decoder < 2 + std::size_t(alpha); ++decoder) {
        for (std::size_t slot = 0; slot < selection_trace::history_slots; ++slot)
            if (matches(left, history[decoder * selection_trace::history_slots + slot], strict)) {
                slots[decoder] = static_cast<int>(slot);
                break;
            }
        if (slots[decoder] < 0) return absent;
    }
    return slots;
}

inline Pair snapshot(const History& history, const Slots& slots) {
    Pair result{};
    for (std::size_t decoder = 0; decoder < slots.size(); ++decoder)
        if (slots[decoder] >= 0)
            result[decoder] = history[decoder * selection_trace::history_slots + slots[decoder]];
    return result;
}

// Preserve the old test decoder.frame(frame_index) lookup for the default,
// including its first-ID behavior if malformed duplicate IDs carry two warps.
// Bounded selection uses pair_slots(..., true) and never uses this lookup.
inline Slots legacy_resource_slots(const History& history, uint64_t frame, bool alpha) {
    Slots result = absent;
    for (std::size_t decoder = 0; decoder < 2 + std::size_t(alpha); ++decoder)
        for (std::size_t slot = 0; slot < selection_trace::history_slots; ++slot) {
            const auto& candidate = history[decoder * selection_trace::history_slots + slot];
            if (candidate.present && candidate.frame == frame) {
                result[decoder] = static_cast<int>(slot);
                break;
            }
        }
    return result;
}

inline bool coherent(const Pair& pair) {
    return matches(pair[0], pair[1], true) && pair[0].view_display_time > 0 &&
           (pair[0].alpha ? matches(pair[0], pair[2], true) : !pair[2].present);
}

// Unsigned subtraction avoids signed-overflow UB even on adversarial targets.
inline uint64_t distance(int64_t a, int64_t b) {
    return a >= b ? uint64_t(a) - uint64_t(b) : uint64_t(b) - uint64_t(a);
}

inline bool not_regressing(const Frame& candidate, const Frame& reference) {
    if (!reference.present) return true;
    if (candidate.frame < reference.frame || candidate.view_display_time < reference.view_display_time)
        return false;
    return candidate.frame != reference.frame ||
           (candidate.warp == reference.warp && candidate.view_display_time == reference.view_display_time &&
            candidate.alpha == reference.alpha);
}

struct Decision {
    bool alpha_required = false, fallback = false;
    int64_t max_ahead = 0;
    Reason reason = Reason::unavailable;
    Slots chosen_slots = absent, closest_slots = absent;
    std::array<bool, selection_trace::history_slots> eligible_left_slots{};
    Pair selected{}, closest{};
};

inline Decision choose(const History& history, const Pair& previous, Policy policy,
                       int64_t target, int64_t period, int64_t previous_target) {
    Decision out;
    out.alpha_required = history[0].present && history[0].alpha;
    out.max_ahead = period_bound(target, period, previous_target);
    uint64_t closest_distance = std::numeric_limits<uint64_t>::max();
    for (int slot = 0; slot < int(selection_trace::history_slots); ++slot) {
        auto slots = pair_slots(history, slot, out.alpha_required, false);
        if (slots[0] < 0) continue;
        const auto delta = distance(history[slot].view_display_time, target);
        if (out.closest_slots[0] < 0 || delta < closest_distance) {
            out.closest_slots = slots;
            closest_distance = delta;
        }
        if (policy == Policy::closest_target) out.eligible_left_slots[slot] = true;
    }
    if (out.closest_slots[0] >= 0)
        out.closest_slots = legacy_resource_slots(history, history[out.closest_slots[0]].frame, out.alpha_required);
    out.closest = snapshot(history, out.closest_slots);
    if (policy == Policy::closest_target && out.closest_slots[0] >= 0) {
        out.chosen_slots = out.closest_slots;
        out.selected = out.closest;
        out.reason = Reason::closest_target;
        return out;
    }
    if (policy == Policy::bounded_newest && out.closest_slots[0] >= 0) {
        for (int slot = 0; slot < int(selection_trace::history_slots); ++slot) {
            const auto slots = pair_slots(history, slot, out.alpha_required, true);
            if (slots[0] < 0) continue;
            const auto& candidate = history[slot];
            if (!not_regressing(candidate, out.closest[0]) ||
                (coherent(previous) && !not_regressing(candidate, previous[0]))) continue;
            if (candidate.view_display_time > target &&
                distance(candidate.view_display_time, target) > uint64_t(out.max_ahead)) continue;
            if (out.max_ahead == 0 && !matches(candidate, out.closest[0], true)) continue;
            out.eligible_left_slots[slot] = true;
            if (out.chosen_slots[0] < 0 || candidate.frame > out.selected[0].frame) {
                out.chosen_slots = slots;
                out.selected = snapshot(history, slots);
            }
        }
        if (out.chosen_slots[0] >= 0) {
            out.reason = Reason::bounded_newest;
            return out;
        }
    }
    out.fallback = true;
    out.reason = out.closest_slots[0] < 0 ? Reason::no_complete_fallback : Reason::no_eligible_fallback;
    if (policy == Policy::closest_target || coherent(previous)) out.selected = previous;
    return out;
}
} // namespace wivrn::codec_test::selection_policy
