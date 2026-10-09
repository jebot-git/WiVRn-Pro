// SPDX-License-Identifier: MIT
#include "codec_test/selection_trace.h"
#include <cassert>
#include <iostream>
#include <sstream>
#include <string>

using namespace wivrn::codec_test::selection_trace;

int main(int argc, char**) {
    Budget disabled;
    assert(!disabled.capture() && !disabled.commit());
    assert(disabled.records == 0 && disabled.dropped == 0);
    Budget active{true};
    for (uint64_t i = 0; i < record_limit; ++i) {
        assert(active.capture());
        assert(active.commit());
        assert(active.records == i + 1 && active.dropped == 0);
    }
    assert(!active.capture());
    for (uint64_t i = 1; i <= 7; ++i) {
        assert(!active.commit());
        assert(active.records == record_limit && active.dropped == i);
    }

    Frame left{true, 9007199254740993ULL, UINT64_MAX, INT64_MIN, 100, 120, false};
    Frame right = left;
    std::array<Frame, decoder_count> pair{left, right, {}};
    assert(same_selection(pair, pair));
    auto changed = pair;
    changed[1].warp--;
    assert(!same_selection(pair, changed));
    changed = pair; changed[1].frame++;
    assert(!same_selection(pair, changed));
    changed = pair; changed[1].present = false;
    assert(!same_selection(pair, changed));
    changed = pair; changed[2] = left;
    assert(!same_selection(pair, changed));
    assert(same_selection(changed, changed));
    assert(!same_selection({}, {}));

    Record record;
    record.sequence = record_limit;
    record.reason = Reason::no_complete_fallback;
    record.repeat_previous = true;
    record.after_fence = true;
    record.selected = pair;
    // Selected fallback need not exist in current history; preserve its own warp.
    record.history[history_slots * 2 + 2] = left;
    record.wait_begin = 1; record.wait_end = 2; record.render_entry = 3;
    record.selection_begin = 4; record.selection_end = 5;
    record.predicted_display = 6; record.end_frame_return = 7;
    auto copied = record;
    record.history[history_slots * 2 + 2].frame = 11;
    assert(copied.history[history_slots * 2 + 2].frame == left.frame);
    std::ostringstream output;
    write_json(output, copied);
    const auto text = output.str();
    assert(text.find("\"schema\":2") != std::string::npos);
    assert(text.find("\"selection_policy\":\"closest-target\"") != std::string::npos);
    assert(text.find("\"max_ahead_xr_ns\":0") != std::string::npos);
    assert(text.find("\"chosen_slots\":[-1,-1,-1]") != std::string::npos);
    assert(text.find("\"eligible_left_slots\":[false,false,false]") != std::string::npos);
    assert(text.find("\"frame\":9007199254740993") != std::string::npos);
    assert(text.find("\"warp\":18446744073709551615") != std::string::npos);
    assert(text.find("\"view_display_time_xr_ns\":-9223372036854775808") != std::string::npos);
    assert(text.find("\"reason\":\"no_complete_fallback\"") != std::string::npos);
    assert(text.find("\"repeat_previous\":true") != std::string::npos);
    assert(text.find("\"decoder\":2,\"slot\":2,\"present\":true") != std::string::npos);
    assert(text.ends_with("}\n"));
    assert(std::string(reason_name(Reason::closest_target)) == "closest_target");
    assert(std::string(reason_name(Reason::bounded_newest)) == "bounded_newest");
    assert(std::string(reason_name(Reason::no_eligible_fallback)) == "no_eligible_fallback");
    assert(std::string(policy_name(Policy::bounded_newest)) == "bounded-newest");
    assert(std::string(reason_name(Reason::unavailable)) == "unavailable");
    // Optional JSON emission permits an independent strict parser round-trip.
    if (argc > 1) std::cout << text;
    else std::cout << "selection trace tests PASS: default-off, cap/drop accounting, exact frame/warp/alpha identities, fixed snapshot, fallback serialization\n";
}
