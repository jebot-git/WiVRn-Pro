/*
 * WiVRn VR streaming
 * Copyright (C) 2024  Guillaume Meunier <guillaume.meunier@centraliens.net>
 * Copyright (C) 2024  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#include "pacer.h"
#ifdef WIVRN_CODEC_TESTS
#include "codec_pacer_trace.h"
#endif

#include "driver/clock_offset.h"

#include "util/u_var.h"
#include <algorithm>
#include <cmath>

#include "os/os_time.h"
#include "util/u_debug.h"

DEBUG_GET_ONCE_FLOAT_OPTION(client_margin_ms, "WIVRN_CLIENT_MARGIN_MS", 1.f)

namespace wivrn
{

static const int64_t margin_ns = 3'000'000;
static const int64_t slop_ns = 500'000;

pacer::pacer(uint64_t frame_duration) :
#ifdef WIVRN_CODEC_TESTS
        // Allocate/open diagnostics before the worker member starts. A path
        // error must not unwind a jthread blocked on the compute condition.
        codec_test_pacer_id(codec_test::pacer_trace().enabled() ? codec_test::next_pacer_trace_id() : 0),
        codec_test_tail_trace(std::getenv("WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES"),
                              std::getenv("WIVRN_CODEC_TEST_PACER_TAIL_TRACE"), codec_test_pacer_id != 0,
                              std::getenv("WIVRN_CODEC_TEST_PACER_BANK_MODE")),
#endif
        frame_duration_ns(frame_duration),
        client_margin_ms{
                .val = debug_get_float_option_client_margin_ms(),
                .step = 0.1,
                .min = 0,
                .max = 20,
        },
        frame_times(5000),
        frame_times_compute(frame_times),
        worker([this](std::stop_token t) {
            std::vector<XrDuration> samples;
#ifdef WIVRN_CODEC_TESTS
            uint64_t consumed_batch = 0;
#endif
            while (not t.stop_requested())
            {
                samples.clear();
#ifdef WIVRN_CODEC_TESTS
                std::vector<frame_time> recent_bank;
                uint64_t recent_epoch = 0, recent_batch = 0;
                int64_t recent_boundary = -1, copy_begin = 0, copy_end = 0;
#endif
                {
                    std::unique_lock lock(compute_mutex);
#ifdef WIVRN_CODEC_TESTS
                    if (codec_test_tail_trace.window) {
                        compute_cv.wait(lock,[&] { return t.stop_requested() || codec_test_tail_batch != consumed_batch; });
                        if (t.stop_requested()) break;
                    } else
#endif
                    compute_cv.wait(lock);
#ifdef WIVRN_CODEC_TESTS
                    if (codec_test_tail_trace.window) {
                        recent_bank = frame_times_compute;
                        consumed_batch = recent_batch = codec_test_tail_batch;
                        recent_epoch = codec_test_tail_compute_epoch;
                        recent_boundary = codec_test_tail_boundary;
                        copy_begin = codec_test_tail_copy_begin_ns;copy_end = codec_test_tail_copy_end_ns;
                    }
#endif
                    for (const auto & time: frame_times_compute)
                    {
                        if (time.decoded > time.present)
                            samples.push_back(time.decoded - time.present);
                    }
                }
#ifdef WIVRN_CODEC_TESTS
                if (codec_test_tail_trace.rolling)
                    codec_test_tail_trace.consume_snapshot(recent_batch,samples.empty());
#endif
                if (samples.empty()) continue;
                auto it = samples.begin() + (samples.size() * 995) / 1000;
                std::ranges::nth_element(samples, it);
                std::unique_lock lock(mutex);
#ifdef WIVRN_CODEC_TESTS
                if (codec_test_tail_trace.window) {
                    const int64_t ns = codec_test::pacer_tail::checked_margin_ns(client_margin_ms.val);
                    const auto decision = codec_test::pacer_tail::choose(recent_bank,
                        codec_test_tail_trace.window, recent_boundary, ns, safe_present_to_decoded_ns, codec_test_tail_newest);
                    const bool stale = recent_epoch != codec_test_tail_epoch;
                    codec_test_tail_trace.append({codec_test_pacer_id,recent_epoch,recent_batch,stale,decision,
                        copy_begin,copy_end,codec_test_tail_trace.rolling ? os_monotonic_get_ns() : 0});
                    if (!stale && decision.valid) {
                        safe_present_to_decoded_ns = decision.applied;
                        codec_test_tail_newest = decision.newest;
                    }
                    continue;
                }
#endif
                safe_present_to_decoded_ns = *it + client_margin_ms.val * U_TIME_1MS_IN_NS;
            }
        })
{
	u_var_add_root(this, "Pacer", false);
	u_var_add_draggable_f32(this, &client_margin_ms, "headset margin (ms)");
}

pacer::~pacer()
{
	u_var_remove_root(this);
#ifdef WIVRN_CODEC_TESTS
    if (codec_test_tail_trace.window) {
        // Synchronize the stop predicate with wait's unlock-and-block step.
        // Otherwise notify can be lost between its false predicate and wait.
        std::lock_guard compute_lock(compute_mutex);
        worker.request_stop();
    } else {
        worker.request_stop();
    }
#else
    worker.request_stop();
#endif
    compute_cv.notify_all();
}

uint64_t pacer::get_frame_duration() const
{
	std::lock_guard lock(mutex);
	return frame_duration_ns;
}

void pacer::set_frame_duration(uint64_t frame_duration_ns)
{
	std::lock_guard lock(mutex);
	this->frame_duration_ns = frame_duration_ns;
}

void pacer::predict(
        int64_t & frame_id,
        int64_t & out_wake_up_time_ns,
        int64_t & out_desired_present_time_ns,
        int64_t & out_present_slop_ns,
        int64_t & out_predicted_display_time_ns)
{
	std::lock_guard lock(mutex);
	frame_id = this->frame_id++;
	auto now = os_monotonic_get_ns();

#ifdef WIVRN_CODEC_TESTS
	const auto previous_client_render_ns = last_ns;
#endif
	int64_t predicted_client_render = last_ns + frame_duration_ns;

	if (now + mean_wake_up_to_present_ns + safe_present_to_decoded_ns > predicted_client_render)
		predicted_client_render += frame_duration_ns * ((now + mean_wake_up_to_present_ns + safe_present_to_decoded_ns - predicted_client_render) / frame_duration_ns);

	out_predicted_display_time_ns = predicted_client_render + mean_render_to_display_ns;
	out_desired_present_time_ns = predicted_client_render - safe_present_to_decoded_ns;
	out_wake_up_time_ns = out_desired_present_time_ns - mean_wake_up_to_present_ns + margin_ns; // we should be awoken early by the application
	last_wake_up_ns = out_wake_up_time_ns;

	last_ns = predicted_client_render;

	in_flight_frames[frame_id % in_flight_frames.size()] = {
	        .frame_id = frame_id,
	        .present_ns = out_desired_present_time_ns,
	        .predicted_display_time = out_predicted_display_time_ns,
	};

	out_present_slop_ns = slop_ns;
#ifdef WIVRN_CODEC_TESTS
	if (codec_test_pacer_id && codec_test::pacer_trace().enabled())
		codec_test::pacer_trace().append({
		        .pacer_id = codec_test_pacer_id,
		        .frame_id = frame_id,
		        .now_ns = now,
		        .previous_client_render_ns = previous_client_render_ns,
		        .period_ns = frame_duration_ns,
		        .wake_to_present_ns = mean_wake_up_to_present_ns,
		        .safe_decode_ns = safe_present_to_decoded_ns,
		        .render_to_display_ns = mean_render_to_display_ns,
		        .margin_ns = margin_ns,
		        .slop_ns = slop_ns,
		        .client_render_ns = predicted_client_render,
		        .desired_present_ns = out_desired_present_time_ns,
		        .wake_up_ns = out_wake_up_time_ns,
		        .display_ns = out_predicted_display_time_ns,
		});
#endif
}

void pacer::on_feedback(const wivrn::from_headset::feedback & feedback, const clock_offset & offset)
{
	if (feedback.times_displayed > 1 or not feedback.blitted)
		return;

	std::lock_guard lock(mutex);
	auto & when = in_flight_frames[feedback.frame_index % in_flight_frames.size()];
	if (when.frame_id != feedback.frame_index)
		return;

	auto & times = frame_times[feedback.frame_index % frame_times.size()];
	if (times.frame_id != feedback.frame_index)
	{
		times.frame_id = feedback.frame_index;
		times.present = when.present_ns;
		times.decoded = 0;
	}
	times.decoded = std::max(times.decoded, offset.from_headset(feedback.received_from_decoder));

	if (feedback.stream_index == 0)
	{
		if (feedback.frame_index % 100 == 0)
		{
			std::unique_lock lock(compute_mutex);
#ifdef WIVRN_CODEC_TESTS
            if (codec_test_tail_trace.rolling) {
                // Keep one cumulative latest-observed-per-frame ring. Feedback
                // for the other eye can still arrive later; this is not a
                // complete-pair claim. The worker sees only this locked copy.
                codec_test_tail_copy_begin_ns = os_monotonic_get_ns();
                std::copy(frame_times.begin(),frame_times.end(),frame_times_compute.begin());
                codec_test_tail_copy_end_ns = os_monotonic_get_ns();
            } else
#endif
            {
                std::swap(frame_times, frame_times_compute);
            }
#ifdef WIVRN_CODEC_TESTS
            if (codec_test_tail_trace.window) {
                codec_test_tail_compute_epoch = codec_test_tail_epoch;
                ++codec_test_tail_batch;
                if (codec_test_tail_trace.rolling) codec_test_tail_trace.produce_snapshot(codec_test_tail_batch);
                codec_test_tail_boundary = frame_id - 1;
            }
#endif
			compute_cv.notify_all();
		}
		// adjust phase
		auto d = (frame_duration_ns / 2 + offset.from_headset(feedback.blitted) - last_ns) % frame_duration_ns;
		if (d < 0)
			d += frame_duration_ns;
		d -= frame_duration_ns / 2;
		auto l = last_ns;
		last_ns += d / 10;
	}

	if (feedback.displayed and feedback.displayed > feedback.blitted and feedback.displayed < feedback.blitted + 100'000'000)
		mean_render_to_display_ns = std::lerp(mean_render_to_display_ns, feedback.displayed - feedback.blitted, 0.1);
}
void pacer::mark_timing_point(
        comp_target_timing_point point,
        int64_t frame_id,
        int64_t when_ns)
{
	switch (point)
	{
		//! Woke up after sleeping in wait frame.
		case COMP_TARGET_TIMING_POINT_WAKE_UP:
			return;

		//! Began CPU side work for GPU.
		case COMP_TARGET_TIMING_POINT_BEGIN:
			return;

		//! Just before submitting work to the GPU.
		case COMP_TARGET_TIMING_POINT_SUBMIT_BEGIN:
			return;

		//! Just after submitting work to the GPU.
		case COMP_TARGET_TIMING_POINT_SUBMIT_END:
			if (when_ns > last_wake_up_ns and when_ns < last_wake_up_ns + 100'000'000)
			{
				std::lock_guard lock(mutex);
				mean_wake_up_to_present_ns = std::lerp(mean_wake_up_to_present_ns, when_ns - last_wake_up_ns, 0.1);
			}
	}
}

pacer::frame_info pacer::present_to_info(int64_t present)
{
	std::lock_guard lock(mutex);
	for (const auto & info: in_flight_frames)
	{
		if (info.present_ns == present)
			return info;
	}
	assert(false);
	return {};
}

void pacer::reset()
{
	std::lock_guard lock(mutex);
	std::ranges::fill(frame_times, frame_time{});
#ifdef WIVRN_CODEC_TESTS
    if (codec_test_tail_trace.window) {
        // Same mutex -> compute_mutex order as feedback; worker releases its
        // compute lock before taking mutex. Old copied snapshots cannot apply.
        std::lock_guard compute_lock(compute_mutex);
        ++codec_test_tail_epoch;
        codec_test_tail_newest = -1;
        ++codec_test_tail_batch;
        codec_test_tail_compute_epoch = codec_test_tail_epoch;
        codec_test_tail_boundary = frame_id - 1;
        std::ranges::fill(frame_times_compute, frame_time{});
        if (codec_test_tail_trace.rolling) {
            codec_test_tail_copy_begin_ns = codec_test_tail_copy_end_ns = os_monotonic_get_ns();
            codec_test_tail_trace.produce_snapshot(codec_test_tail_batch);
        }
    }
#endif
}
} // namespace wivrn
