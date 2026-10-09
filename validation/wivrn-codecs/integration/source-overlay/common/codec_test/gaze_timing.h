// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>
#include <limits>

namespace wivrn::codec_test {
// All times are in the local OpenXR clock domain. The returned EXT time is
// pose-expression time, possibly predicted/interpolated; never sensor capture time.
struct gaze_timing_sample {
    int64_t scheduled_poll_xr_ns = 0;
    int64_t poll_begin_xr_ns = 0;
    int64_t poll_end_xr_ns = 0;
    int64_t requested_pose_xr_ns = 0;
    int64_t returned_pose_expression_xr_ns = 0;
    int32_t locate_result = -1;
    uint64_t location_flags = 0;

    bool locate_succeeded() const { return locate_result >= 0; }
    bool time_available() const { return locate_succeeded() && returned_pose_expression_xr_ns != 0; }
    bool expression_after_poll() const {
        return time_available() && returned_pose_expression_xr_ns > poll_end_xr_ns;
    }
    // OpenXR XrSpaceLocationFlagBits, kept here without a runtime dependency.
    bool orientation_valid() const { return locate_succeeded() && (location_flags & 1); }
    bool position_valid() const { return locate_succeeded() && (location_flags & 2); }
    bool orientation_tracked() const { return locate_succeeded() && (location_flags & 4); }
    bool position_tracked() const { return locate_succeeded() && (location_flags & 8); }
    bool poll_order_valid() const { return poll_end_xr_ns >= poll_begin_xr_ns; }
};

// One instance per stream. Count every instrumented locate, publish a bounded
// subset. No extrapolation, freshness inference or acquisition-age calculation.
struct gaze_timing_limiter {
    static constexpr int64_t interval_ns = 100'000'000;
    static constexpr size_t max_rows = 6000;
    uint64_t sequence = 0;
    size_t rows = 0;
    int64_t last_emit_xr_ns = 0;

    bool observe(int64_t poll_begin_xr_ns) {
        if (sequence == std::numeric_limits<uint64_t>::max()) return false;
        ++sequence;
        if (rows >= max_rows) return false;
        // A backwards clock does not produce a burst or restart the cap.
        if (rows && (poll_begin_xr_ns <= last_emit_xr_ns ||
                     uint64_t(poll_begin_xr_ns) - uint64_t(last_emit_xr_ns) < uint64_t(interval_ns)))
            return false;
        last_emit_xr_ns = poll_begin_xr_ns;
        ++rows;
        return true;
    }
};
}
