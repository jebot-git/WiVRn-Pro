// SPDX-License-Identifier: MIT
#pragma once
#include "codec_test/selection_policy.h"

namespace wivrn::codec_test::horizon_policy {
namespace sp = selection_policy;
namespace st = selection_trace;

struct Config {
    bool enabled = false;
    uint32_t numerator = 1, denominator = 1;
};
struct Result {
    bool valid_input = true;
    sp::Decision decision;
};

// Opt-in standalone research policy. Disabled is exactly legacy closest-target.
// Fraction 1/1 equals existing bounded-newest. No new resource ownership or
// admission slot is introduced. A fraction limits future replacement targets;
// an existing coherent retained fallback keeps its original target metadata.
inline Result choose(const sp::History& history, const sp::Pair& previous,
                     int64_t target, int64_t period, int64_t prior_target, Config config = {}) {
    if (!config.enabled)
        return {true, sp::choose(history, previous, st::Policy::closest_target, target, period, prior_target)};
    if (config.denominator == 0 || config.denominator > 1024 || config.numerator > config.denominator ||
        target <= 0 || period < 0 || prior_target < 0 || prior_target >= target)
        return {false, {}};
    auto out = sp::choose(history, previous, st::Policy::closest_target, target, period, prior_target);
    const auto full = static_cast<uint64_t>(sp::period_bound(target, period, prior_target));
    // Division first keeps every intermediate bounded even at INT64_MAX.
    out.max_ahead = static_cast<int64_t>((full / config.denominator) * config.numerator +
        ((full % config.denominator) * config.numerator) / config.denominator);
    out.chosen_slots = sp::absent;
    out.selected = {};
    out.eligible_left_slots.fill(false);
    if (out.closest_slots[0] >= 0) {
        for (int slot = 0; slot < int(st::history_slots); ++slot) {
            const auto slots = sp::pair_slots(history, slot, out.alpha_required, true);
            if (slots[0] < 0) continue;
            const auto& candidate = history[slot];
            if (!sp::not_regressing(candidate, out.closest[0]) ||
                (sp::coherent(previous) && !sp::not_regressing(candidate, previous[0]))) continue;
            if (candidate.view_display_time > target &&
                sp::distance(candidate.view_display_time, target) > static_cast<uint64_t>(out.max_ahead)) continue;
            if (out.max_ahead == 0 && !sp::matches(candidate, out.closest[0], true)) continue;
            out.eligible_left_slots[slot] = true;
            if (out.chosen_slots[0] < 0 || candidate.frame > out.selected[0].frame) {
                out.chosen_slots = slots;
                out.selected = sp::snapshot(history, slots);
            }
        }
    }
    out.fallback = out.chosen_slots[0] < 0;
    out.reason = out.fallback ? (out.closest_slots[0] < 0 ? st::Reason::no_complete_fallback : st::Reason::no_eligible_fallback)
                              : st::Reason::bounded_newest;
    if (out.fallback && sp::coherent(previous)) out.selected = previous;
    return {true, out};
}
}
