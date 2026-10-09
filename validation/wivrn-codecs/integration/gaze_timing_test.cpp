// SPDX-License-Identifier: MIT
#include "codec_test/gaze_timing.h"
#include <cassert>
#include <limits>

int main() {
    using namespace wivrn::codec_test;
    gaze_timing_sample sample;
    sample.poll_begin_xr_ns = 100;
    sample.poll_end_xr_ns = 120;
    sample.requested_pose_xr_ns = 200;
    sample.locate_result = 0;
    sample.location_flags = 15;
    // Valid pose does not make a missing expression timestamp available.
    assert(sample.orientation_valid() && sample.position_valid());
    assert(sample.orientation_tracked() && sample.position_tracked());
    assert(!sample.time_available() && !sample.expression_after_poll());
    sample.returned_pose_expression_xr_ns = 200;
    assert(sample.time_available() && sample.expression_after_poll());
    // Runtime prediction is preserved, not rejected or converted to sensor age.
    assert(sample.returned_pose_expression_xr_ns == sample.requested_pose_xr_ns);
    sample.returned_pose_expression_xr_ns = 80;
    assert(sample.time_available() && !sample.expression_after_poll());
    sample.location_flags = 1;
    assert(sample.orientation_valid() && !sample.position_valid());
    assert(!sample.orientation_tracked() && !sample.position_tracked());
    sample.location_flags = 4;
    assert(!sample.orientation_valid() && sample.orientation_tracked());
    sample.locate_result = -1;
    sample.location_flags = 15;
    assert(!sample.time_available() && !sample.expression_after_poll());
    assert(!sample.orientation_valid() && !sample.position_valid());
    assert(!sample.orientation_tracked() && !sample.position_tracked());
    assert(sample.poll_order_valid());
    sample.poll_end_xr_ns = 90;
    assert(!sample.poll_order_valid());

    gaze_timing_limiter limiter;
    assert(limiter.observe(1));
    assert(!limiter.observe(1));
    assert(!limiter.observe(0));
    assert(!limiter.observe(100'000'000));
    assert(limiter.observe(100'000'001));
    assert(limiter.sequence == 5 && limiter.rows == 2);
    for (size_t row = 2; row < gaze_timing_limiter::max_rows; ++row)
        assert(limiter.observe(int64_t(row) * gaze_timing_limiter::interval_ns + 1));
    assert(!limiter.observe(std::numeric_limits<int64_t>::max()));
    assert(limiter.rows == gaze_timing_limiter::max_rows);
    limiter.sequence = std::numeric_limits<uint64_t>::max();
    assert(!limiter.observe(std::numeric_limits<int64_t>::max()));
    assert(limiter.sequence == std::numeric_limits<uint64_t>::max());

    // Difference handling has no signed overflow, even at clock extrema.
    gaze_timing_limiter extremes;
    assert(extremes.observe(std::numeric_limits<int64_t>::min()));
    assert(extremes.observe(std::numeric_limits<int64_t>::max()));
    assert(!extremes.observe(std::numeric_limits<int64_t>::min()));
}
