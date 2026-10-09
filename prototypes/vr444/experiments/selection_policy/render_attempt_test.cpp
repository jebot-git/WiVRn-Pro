// SPDX-License-Identifier: MIT
#include "codec_test/selection_trace.h"
#include <cassert>
#include <iostream>
#include <limits>
#include <sstream>
#include <string>
#include <tuple>

namespace st = wivrn::codec_test::selection_trace;

int main(int argc, char**) {
    st::AttemptLedger disabled;
    { st::AttemptGuard guard(disabled, 10, 0); }
    assert(disabled.submit().render_attempt == 0);

    st::AttemptLedger ledger;
    ledger.enabled = true;
    for (std::size_t i = 0; i < st::skip_names.size(); ++i) {
        st::AttemptGuard guard(ledger, (i + 1) * 10, i * 10);
        ledger.skip(static_cast<st::Skip>(i));
    }
    st::Attempts first, second;
    {
        st::AttemptGuard guard(ledger, 60, 50);
        first = ledger.submit();
        assert(first.valid && first.render_attempt == 6 && first.previous_submission_attempt == 0);
        assert(first.previous_submission_target == 0 && first.aborted == 0);
        for (auto n : first.skipped) assert(n == 1);
    }
    {
        st::AttemptGuard guard(ledger, 70, 60);
        second = ledger.submit();
        assert(second.valid && second.render_attempt == 7 && second.previous_submission_attempt == 6);
        assert(second.previous_submission_target == 60);
        for (auto n : second.skipped) assert(n == 0);
    }
    // An uncategorized return or exception is sticky invalid even after a
    // subsequent successful submit; it cannot be relabeled as a benign skip.
    { st::AttemptGuard guard(ledger, 80, 70); }
    {
        st::AttemptGuard guard(ledger, 90, 80);
        const auto bad = ledger.submit();
        assert(!bad.valid && bad.aborted == 1 && bad.render_attempt == 9);
    }
    {
        st::AttemptGuard guard(ledger, 100, 90);
        assert(!ledger.submit().valid);
    }
    // Duplicate finish, invalid reason, target regression and wrong predecessor.
    for (int which = 0; which < 5; ++which) {
        st::AttemptLedger invalid;
        invalid.enabled = true;
        invalid.begin(10, 0);
        if (which == 0) { invalid.skip(st::Skip::no_complete_pair); invalid.skip(st::Skip::no_complete_pair); }
        if (which == 1) invalid.skip(static_cast<st::Skip>(999));
        if (which == 2) { invalid.submit(); invalid.begin(9, 10); }
        if (which == 3) { invalid.submit(); invalid.begin(20, 9); }
        if (which == 4) invalid.begin(20, 10); // Previous active attempt was never finished.
        assert(!invalid.submit().valid);
    }
    uint64_t count = std::numeric_limits<int64_t>::max() - 1;
    assert(st::increment_attempt_count(count));
    assert(count == static_cast<uint64_t>(std::numeric_limits<int64_t>::max()));
    assert(!st::increment_attempt_count(count));
    count = std::numeric_limits<uint64_t>::max();
    assert(!st::increment_attempt_count(count) && count == std::numeric_limits<uint64_t>::max());

    st::Record record;
    record.schema = 3;
    record.attempts = first;
    std::ostringstream stream;
    st::write_json(stream, record);
    assert(stream.str().find("\"schema\":3") != std::string::npos);
    assert(stream.str().find("\"render_attempts\":{\"render_attempt\":6") != std::string::npos);
    if (argc > 1) {
        for (const auto& [target, prior, snapshot] : {
                 std::tuple<int64_t, int64_t, st::Attempts>{60, 50, first}, {70, 60, second}}) {
            std::cout << "{\"predicted_display_xr_ns\":" << target
                      << ",\"previous_predicted_display_xr_ns\":" << prior << ",\"render_attempts\":";
            st::write_attempts(std::cout, snapshot);
            std::cout << "}\n";
        }
    } else {
        std::cout << "Render-attempt accounting PASS: startup, all skip reasons, submissions, "
                     "abort/duplicate/missing completion, clock links, overflow, schema-3 serialization\n";
    }
}
