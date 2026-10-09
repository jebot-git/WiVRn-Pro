// SPDX-License-Identifier: MIT
#pragma once
#include "horizon_policy.h"

namespace wivrn::codec_test::ready_successor_policy {
namespace hp = horizon_policy;
namespace sp = selection_policy;
namespace st = selection_trace;
struct Config {
    bool enabled = false;
    bool require_successor = true;
    uint32_t numerator = 1, denominator = 1;
};
struct Result : hp::Result {
    std::array<int, st::history_slots> successor_left_slots{-1, -1, -1};
};

// A snapshot condition, not resource reservation. No additional handle is owned.
// Only an advancement beyond closest-target needs a complete ready successor,
// whose target is later and no further than the second current horizon.
inline Result choose(const sp::History& history, const sp::Pair& previous,
                     int64_t target, int64_t period, int64_t prior, Config config = {}) {
    Result result;
    static_cast<hp::Result&>(result) = hp::choose(history, previous, target, period, prior,
                                                {config.enabled, config.numerator, config.denominator});
    if (!config.enabled || !config.require_successor || !result.valid_input) return result;
    auto& out = result.decision;
    const auto horizon = static_cast<uint64_t>(out.max_ahead);
    for (int slot = 0; slot < int(st::history_slots); ++slot) {
        if (!out.eligible_left_slots[slot]) continue;
        const auto& candidate = history[slot];
        if (candidate.frame <= out.closest[0].frame) continue;
        for (int successor = 0; successor < int(st::history_slots); ++successor) {
            const auto& next = history[successor];
            if (!next.present || next.frame <= candidate.frame || next.view_display_time <= candidate.view_display_time)
                continue;
            if (sp::pair_slots(history, successor, out.alpha_required, true)[0] < 0) continue;
            // max_ahead <= INT64_MAX, so doubling it remains representable in uint64_t.
            if (next.view_display_time > target && sp::distance(next.view_display_time, target) > 2 * horizon) continue;
            result.successor_left_slots[slot] = successor;
            break;
        }
        if (result.successor_left_slots[slot] < 0) out.eligible_left_slots[slot] = false;
    }
    out.chosen_slots = sp::absent;
    out.selected = {};
    for (int slot = 0; slot < int(st::history_slots); ++slot)
        if (out.eligible_left_slots[slot] && (out.chosen_slots[0] < 0 || history[slot].frame > out.selected[0].frame)) {
            out.chosen_slots = sp::pair_slots(history, slot, out.alpha_required, true);
            out.selected = sp::snapshot(history, out.chosen_slots);
        }
    out.fallback = out.chosen_slots[0] < 0;
    out.reason = out.fallback ? (out.closest_slots[0] < 0 ? st::Reason::no_complete_fallback : st::Reason::no_eligible_fallback)
                              : st::Reason::bounded_newest;
    if (out.fallback && sp::coherent(previous)) out.selected = previous;
    return result;
}
}
