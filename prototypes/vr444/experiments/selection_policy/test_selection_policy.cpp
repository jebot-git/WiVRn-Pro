// SPDX-License-Identifier: MIT
#include "codec_test/selection_policy.h"
#include <cassert>
#include <iostream>
#include <random>
#include <sstream>
#include <string>
#include <tuple>
#include <vector>

namespace st = wivrn::codec_test::selection_trace;
namespace sp = wivrn::codec_test::selection_policy;

static st::Frame frame(uint64_t id, int64_t target, uint64_t warp = 123, bool alpha = false) {
    return {true, id, warp, target, 10, 20, alpha};
}
static void pair(sp::History& history, int slot, const st::Frame& value, bool include_alpha = false) {
    history[slot] = history[3 + slot] = value;
    if (include_alpha) history[6 + slot] = value;
}
static bool equal(const st::Frame& a, const st::Frame& b) {
    return std::tie(a.present, a.frame, a.warp, a.view_display_time, a.receive, a.ready, a.alpha) ==
           std::tie(b.present, b.frame, b.warp, b.view_display_time, b.receive, b.ready, b.alpha);
}
static bool equal(const sp::Pair& a, const sp::Pair& b) {
    for (size_t i = 0; i < a.size(); ++i) if (!equal(a[i], b[i])) return false;
    return true;
}
struct Fixture { std::string name; st::Record record; };
static std::vector<Fixture> fixtures;
static sp::Decision exercise(const std::string& name, const sp::History& history,
                             const sp::Pair& previous = {}, st::Policy policy = st::Policy::bounded_newest,
                             int64_t target = 1000, int64_t period = 100, int64_t previous_target = 900) {
    const auto result = sp::choose(history, previous, policy, target, period, previous_target);
    st::Record record;
    record.sequence = fixtures.size() + 1;
    record.policy = policy; record.predicted_display = target;
    record.predicted_period = period; record.previous_predicted_display = previous_target;
    record.max_ahead = result.max_ahead;
    record.history = history; record.previous = previous; record.closest = result.closest;
    record.selected = result.selected; record.chosen_slots = result.chosen_slots;
    record.eligible_left_slots = result.eligible_left_slots; record.reason = result.reason;
    record.alpha_required = result.alpha_required;
    record.selected_alpha_required = result.selected[0].present && result.selected[0].alpha;
    record.repeat_previous = st::same_selection(result.selected, previous);
    fixtures.push_back({name, record});
    return result;
}

// Independent transcription of the prior candidate scan + decoder.frame(id).
static sp::Pair old_default(const sp::History& h, const sp::Pair& previous, int64_t target) {
    const auto count = 2 + size_t(h[0].present && h[0].alpha);
    int closest = -1;
    for (int slot = 0; slot < 3; ++slot) {
        if (!h[slot].present) continue;
        bool complete = true;
        for (size_t decoder = 1; decoder < count; ++decoder) {
            bool found = false;
            for (int other = 0; other < 3; ++other) {
                const auto& r = h[decoder * 3 + other];
                found |= r.present && r.frame == h[slot].frame && r.warp == h[slot].warp;
            }
            complete &= found;
        }
        if (complete && (closest < 0 || std::abs(h[slot].view_display_time - target) <
                                       std::abs(h[closest].view_display_time - target))) closest = slot;
    }
    if (closest < 0) return previous;
    sp::Pair selected{};
    for (size_t decoder = 0; decoder < count; ++decoder)
        for (int slot = 0; slot < 3; ++slot) {
            const auto& f = h[decoder * 3 + slot];
            if (f.present && f.frame == h[closest].frame) { selected[decoder] = f; break; }
        }
    return selected;
}

int main(int argc, char**) {
    using st::Policy;
    using st::Reason;
    assert(sp::period_bound(1000, 100, 900) == 100);
    assert(sp::period_bound(1000, 100, 960) == 40);
    assert(sp::period_bound(1000, 100, 0) == 0);
    assert(sp::period_bound(1000, 0, 900) == 0);
    assert(sp::period_bound(1000, -1, 900) == 0);
    assert(sp::period_bound(1000, 100, 1000) == 0);
    assert(sp::period_bound(1000, 100, 1100) == 0);
    assert(sp::period_bound(INT64_MAX, INT64_MAX, 1) == INT64_MAX - 1);
    assert(sp::distance(INT64_MIN, INT64_MAX) == UINT64_MAX);

    sp::History h{};
    auto d = exercise("empty", h);
    assert(d.reason == Reason::no_complete_fallback && !d.selected[0].present);
    h[0] = frame(10, 1000);
    d = exercise("partial-eye", h);
    assert(d.reason == Reason::no_complete_fallback);
    h[3] = frame(10, 1000, 124);
    d = exercise("mismatched-warp", h);
    assert(d.reason == Reason::no_complete_fallback);
    h[3] = frame(11, 1000);
    d = exercise("mismatched-id", h);
    assert(d.reason == Reason::no_complete_fallback);
    pair(h, 0, frame(10, 1000));
    pair(h, 1, frame(11, 1100));
    d = exercise("inclusive-one-period-bound", h);
    assert(d.selected[0].frame == 11 && d.closest[0].frame == 10);
    assert((d.chosen_slots == sp::Slots{1, 1, -1}));
    d = exercise("default-closest", h, {}, Policy::closest_target);
    assert(d.selected[0].frame == 10 && d.reason == Reason::closest_target);
    pair(h, 1, frame(11, 1101));
    d = exercise("beyond-one-period", h);
    assert(d.selected[0].frame == 10 && !d.eligible_left_slots[1]);
    pair(h, 1, frame(11, 1090));
    d = exercise("observed-period-tightens-bound", h, {}, Policy::bounded_newest, 1000, 100, 950);
    assert(d.max_ahead == 50 && d.selected[0].frame == 10);
    d = exercise("first-runtime-target-disables-advancement", h, {}, Policy::bounded_newest, 1000, 100, 0);
    assert(d.max_ahead == 0 && d.selected[0].frame == 10);
    d = exercise("invalid-period-disables-advancement", h, {}, Policy::bounded_newest, 1000, 0, 900);
    assert(d.max_ahead == 0 && d.selected[0].frame == 10);
    pair(h, 0, frame(10, 1001)); pair(h, 1, frame(11, 1090));
    d = exercise("zero-bound-rejects-future-closest", h, {}, Policy::bounded_newest, 1000, 0, 900);
    assert(d.reason == Reason::no_eligible_fallback);

    h = {}; pair(h, 2, frame(12, 1100)); pair(h, 0, frame(10, 1000)); pair(h, 1, frame(11, 1050));
    d = exercise("highest-id-arrives-out-of-order", h);
    assert(d.selected[0].frame == 12);
    h = {}; pair(h, 0, frame(10, 1000)); pair(h, 1, frame(12, 1020)); pair(h, 2, frame(11, 1080));
    d = exercise("id-priority-among-nonregressing-targets", h);
    assert(d.selected[0].frame == 12);
    h = {}; pair(h, 0, frame(10, 1000)); pair(h, 1, frame(12, 900));
    d = exercise("newer-id-regressive-target", h);
    assert(d.selected[0].frame == 10 && !d.eligible_left_slots[1]);
    h = {}; pair(h, 0, frame(10, 1000)); pair(h, 1, frame(9, 1050));
    d = exercise("future-target-regressive-id", h);
    assert(d.selected[0].frame == 10 && !d.eligible_left_slots[1]);
    h = {}; pair(h, 0, frame(11, 950)); pair(h, 1, frame(12, 1050));
    d = exercise("default-distance-tie-first-slot", h, {}, Policy::closest_target);
    assert(d.selected[0].frame == 11);
    d = exercise("bounded-distance-tie-advances", h);
    assert(d.selected[0].frame == 12);

    sp::Pair previous{frame(12, 1050), frame(12, 1050), {}};
    h = {}; pair(h, 0, frame(10, 1000)); pair(h, 1, frame(11, 1040));
    d = exercise("previous-id-blocks-regression", h, previous);
    assert(d.reason == Reason::no_eligible_fallback && equal(d.selected, previous));
    assert(d.chosen_slots == sp::absent);
    pair(h, 1, frame(13, 1040));
    d = exercise("previous-target-blocks-regression", h, previous);
    assert(d.reason == Reason::no_eligible_fallback && equal(d.selected, previous));
    pair(h, 1, frame(12, 1050, 999));
    d = exercise("same-previous-id-different-warp-rejected", h, previous);
    assert(d.reason == Reason::no_eligible_fallback);
    pair(h, 1, frame(12, 1060));
    d = exercise("same-previous-id-different-target-rejected", h, previous);
    assert(d.reason == Reason::no_eligible_fallback);
    pair(h, 1, frame(12, 1050));
    d = exercise("same-previous-identity-permitted", h, previous);
    assert(d.selected[0].frame == 12 && d.chosen_slots[0] == 1);
    pair(h, 1, frame(13, 1050));
    d = exercise("newer-id-same-target-permitted-not-freshness-proof", h, previous);
    assert(d.selected[0].frame == 13);
    h = {};
    d = exercise("coherent-previous-retained-on-partial-history", h, previous);
    assert(d.reason == Reason::no_complete_fallback && equal(d.selected, previous));
    auto malformed = previous; malformed[1].warp++;
    d = exercise("bounded-malformed-previous-rejected", h, malformed);
    assert(!d.selected[0].present);
    d = exercise("default-malformed-previous-preserved", h, malformed, Policy::closest_target);
    assert(equal(d.selected, malformed));

    h = {}; pair(h, 0, frame(10, 1000, 123, true));
    d = exercise("alpha-required-but-missing", h);
    assert(d.alpha_required && d.reason == Reason::no_complete_fallback);
    h[6] = frame(10, 1000, 123, true);
    d = exercise("complete-alpha-triplet", h);
    assert(d.alpha_required && d.selected[2].present);
    h[6].warp++;
    d = exercise("alpha-warp-mismatch", h);
    assert(d.reason == Reason::no_complete_fallback);
    h = {}; pair(h, 0, frame(10, 1000, 123, true));
    d = exercise("fallback-keeps-previous-alpha-mode", h, previous);
    assert(d.alpha_required && !d.selected[0].alpha && !d.selected[2].present);
    h = {}; pair(h, 0, frame(10, 1000));
    h[3].view_display_time++;
    d = exercise("bounded-target-metadata-mismatch", h);
    assert(d.reason == Reason::no_eligible_fallback);
    d = exercise("default-legacy-warp-only-eligibility", h, {}, Policy::closest_target);
    assert(d.reason == Reason::closest_target && d.selected[1].view_display_time == 1001);
    h[3].view_display_time = 1000; h[3].alpha = true;
    d = exercise("bounded-alpha-metadata-mismatch", h);
    assert(d.reason == Reason::no_eligible_fallback);
    h = {}; pair(h, 0, frame(10, 1000)); pair(h, 1, frame(10, 1000, 124));
    d = exercise("same-id-alternative-warp-cannot-advance", h);
    assert(d.chosen_slots[0] == 0 && !d.eligible_left_slots[1]);
    h = {}; h[0] = frame(10, 1000); h[3] = frame(10, 1000, 124); h[4] = frame(10, 1000);
    d = exercise("default-first-id-resource-lookup-preserved", h, {}, Policy::closest_target);
    assert(d.chosen_slots[1] == 0 && d.selected[1].warp == 124);
    d = exercise("bounded-exact-warp-resource-lookup", h);
    assert(d.chosen_slots[1] == 1 && d.selected[1].warp == 123);
    h = {}; pair(h, 0, frame(UINT64_MAX - 1, 1000)); pair(h, 1, frame(UINT64_MAX, 1100));
    d = exercise("uint64-frame-boundary", h);
    assert(d.selected[0].frame == UINT64_MAX);
    h = {}; pair(h, 0, frame(10, INT64_MAX - 10)); pair(h, 1, frame(11, INT64_MAX));
    d = exercise("signed-target-boundary", h, {}, Policy::bounded_newest, INT64_MAX - 10, 100, INT64_MAX - 110);
    assert(d.selected[0].frame == 11);

    std::mt19937_64 random(0x44415);
    size_t bounded_choices = 0;
    for (int iteration = 0; iteration < 20000; ++iteration) {
        h = {};
        for (auto& value : h)
            value = {bool(random() % 3), random() % 5, random() % 3,
                     int64_t(800 + random() % 400), 10, 20, bool(random() % 2)};
        if (iteration % 2 == 0) {
            h = {};
            const bool alpha = random() % 2;
            for (int slot = 0; slot < 3; ++slot)
                pair(h, slot, frame(random() % 5, int64_t(800 + random() % 400), random() % 3, alpha), alpha);
        }
        const auto legacy = sp::choose(h, malformed, Policy::closest_target, 1000, 100, 900);
        assert(equal(legacy.selected, old_default(h, malformed, 1000)));
        const auto random_old = frame(random() % 5, int64_t(800 + random() % 400), random() % 3);
        const sp::Pair random_previous = iteration % 3 ? sp::Pair{random_old, random_old, {}} : sp::Pair{};
        const auto bounded = sp::choose(h, random_previous, Policy::bounded_newest, 1000, 100, 900);
        if (!bounded.fallback) {
            ++bounded_choices;
            assert(sp::coherent(bounded.selected));
            assert(sp::not_regressing(bounded.selected[0], random_previous[0]));
            assert(sp::not_regressing(bounded.selected[0], bounded.closest[0]));
            assert(bounded.selected[0].view_display_time <= 1100);
            assert(equal(bounded.selected, sp::snapshot(h, bounded.chosen_slots)));
        }
    }
    assert(bounded_choices > 100);
    if (argc > 1) {
        for (const auto& fixture : fixtures) {
            std::ostringstream out;
            st::write_json(out, fixture.record);
            auto json = out.str(); json.pop_back();
            std::cout << "{\"case\":\"" << fixture.name << "\",\"record\":" << json << "}\n";
        }
    } else {
        std::cout << "selection policy PASS: " << fixtures.size()
                  << " semantic cases; 20000 independent legacy replays; " << bounded_choices << " bounded selections checked\n";
    }
}
