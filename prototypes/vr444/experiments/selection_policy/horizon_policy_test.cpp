// SPDX-License-Identifier: MIT
#include "horizon_policy.h"
#include <cassert>
#include <iostream>
#include <limits>
#include <random>
#include <sstream>
#include <tuple>

namespace hp = wivrn::codec_test::horizon_policy;
namespace sp = wivrn::codec_test::selection_policy;
namespace st = wivrn::codec_test::selection_trace;

static st::Frame frame(uint64_t id, int64_t target, bool alpha = false) {
    return {true, id, id+10, target, 10, 20, alpha};
}
static void pair(sp::History& h, int slot, st::Frame f) {
    h[slot] = h[3+slot] = f;
    if (f.alpha) h[6+slot] = f;
}
static bool equal(const st::Frame& a, const st::Frame& b) {
    return std::tie(a.present, a.frame, a.warp, a.view_display_time, a.receive, a.ready, a.alpha) ==
           std::tie(b.present, b.frame, b.warp, b.view_display_time, b.receive, b.ready, b.alpha);
}
template <size_t N> static bool equal(const std::array<st::Frame, N>& a, const std::array<st::Frame, N>& b) {
    for (size_t i=0; i<N; ++i) if (!equal(a[i], b[i])) return false;
    return true;
}
static bool equal(const sp::Decision& a, const sp::Decision& b) {
    return a.alpha_required == b.alpha_required && a.fallback == b.fallback && a.max_ahead == b.max_ahead &&
           a.reason == b.reason && a.chosen_slots == b.chosen_slots && a.closest_slots == b.closest_slots &&
           a.eligible_left_slots == b.eligible_left_slots && equal(a.selected, b.selected) && equal(a.closest, b.closest);
}
static st::Record record(const sp::History& h, const sp::Pair& previous, const sp::Decision& d,
                         int64_t target=1000, int64_t period=100, int64_t prior=900) {
    st::Record r;
    r.policy = st::Policy::bounded_newest;
    r.history = h; r.previous = previous; r.closest = d.closest; r.selected = d.selected;
    r.predicted_display = target; r.predicted_period = period; r.previous_predicted_display = prior;
    r.alpha_required = d.alpha_required; r.selected_alpha_required = d.selected[0].present && d.selected[0].alpha;
    r.max_ahead = d.max_ahead; r.chosen_slots = d.chosen_slots; r.eligible_left_slots = d.eligible_left_slots;
    r.reason = d.reason;
    return r;
}
static std::string json(const st::Record& r) {
    std::ostringstream out; st::write_json(out, r);
    auto value = out.str(); value.pop_back(); return value;
}

int main(int argc, char**) {
    sp::History h{};
    pair(h, 0, frame(1, 1000)); pair(h, 1, frame(2, 1050)); pair(h, 2, frame(3, 1100));
    assert(hp::choose(h, {}, 1000, 100, 900).decision.selected[0].frame == 1);
    for (const auto [n,d,want] : {std::tuple<uint32_t,uint32_t,uint64_t>{0,1,1}, {1,2,2}, {3,4,2}, {1,1,3}}) {
        const auto result = hp::choose(h, {}, 1000, 100, 900, {true,n,d});
        assert(result.valid_input && result.decision.selected[0].frame == want);
    }
    // A candidate exactly at a fractional boundary is admissible; +1 is not.
    pair(h, 1, frame(2, 1051));
    assert(hp::choose(h, {}, 1000, 100, 900, {true,1,2}).decision.selected[0].frame == 1);
    // Missing alpha/eye identity cannot be admitted by a larger horizon.
    pair(h, 0, frame(1, 1000, true)); pair(h, 1, frame(2, 1050, true));
    h[7] = {}; h[5].warp++;
    assert(hp::choose(h, {}, 1000, 100, 900, {true,1,1}).decision.selected[0].frame == 1);
    for (const hp::Config bad : {hp::Config{true,1,0}, {true,2,1}, {true,1,1025}})
        assert(!hp::choose(h, {}, 1000, 100, 900, bad).valid_input);
    for (const auto [target,period,prior] : {std::tuple<int64_t,int64_t,int64_t>{0,1,0}, {10,-1,0}, {10,1,-1}, {10,1,10}})
        assert(!hp::choose(h, {}, target, period, prior, {true,1,1}).valid_input);
    const auto high = std::numeric_limits<int64_t>::max();
    auto large = hp::choose({}, {}, high, high, 1, {true,1023,1024});
    assert(large.valid_input && large.decision.max_ahead >= 0 && large.decision.max_ahead < high);

    std::mt19937_64 random(0x5e1ec7);
    uint64_t admitted = 0;
    const std::array<hp::Config, 5> configs{{{true,0,1}, {true,1,2}, {true,3,4}, {true,31,32}, {true,1,1}}};
    for (int i=0; i<20000; ++i) {
        sp::History history{};
        sp::Pair previous{};
        for (int slot=0; slot<3; ++slot) {
            if (random()%4 == 0) continue;
            pair(history, slot, frame(random()%30, 800+random()%400, random()%4 == 0));
            if (random()%7 == 0) history[3+slot].warp++;
            if (random()%7 == 0) history[3+slot] = {};
        }
        if (random()%3 == 0) previous = {frame(random()%30, 900), {}, {}};
        if (random()%2 == 0) previous[1] = previous[0];
        const int64_t prior = i%11 == 0 ? 0 : 900;
        const auto legacy = sp::choose(history, previous, st::Policy::closest_target, 1000, 100, prior);
        const auto bounded = sp::choose(history, previous, st::Policy::bounded_newest, 1000, 100, prior);
        assert(equal(hp::choose(history, previous, 1000, 100, prior).decision, legacy));
        assert(equal(hp::choose(history, previous, 1000, 100, prior, {true,1,1}).decision, bounded));
        const auto config = configs[i%configs.size()];
        const auto result = hp::choose(history, previous, 1000, 100, prior, config);
        assert(result.valid_input);
        const auto& d = result.decision;
        if (!d.fallback) {
            ++admitted;
            assert(sp::coherent(d.selected));
            assert(sp::not_regressing(d.selected[0], d.closest[0]));
            if (sp::coherent(previous)) assert(sp::not_regressing(d.selected[0], previous[0]));
            assert(d.selected[0].view_display_time <= 1000 + d.max_ahead);
        } else {
            assert(sp::coherent(previous) ? equal(d.selected, previous) : equal(d.selected, sp::Pair{}));
        }
        if (argc > 1 && i < 256)
            std::cout << "{\"synthetic\":true,\"numerator\":" << config.numerator << ",\"denominator\":" << config.denominator
                      << ",\"input\":" << json(record(history, previous, legacy, 1000, 100, prior))
                      << ",\"expected\":" << json(record(history, previous, d, 1000, 100, prior)) << "}\n";
    }
    if (argc == 1) std::cout << "Horizon policy PASS: 20000 disabled and full-horizon equivalence cases, "
                             << admitted << " fractional admissions, boundary/alpha/malformed-config/overflow checks\n";
}
