/*
 * WiVRn VR streaming
 * Copyright (C) 2025  Patrick Nicolas <patricknicolas@laposte.net>
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

#include "app_pacer.h"
#include "codec_source_trace.h"
#ifdef WIVRN_CODEC_TESTS
#include "os/os_time.h"
#endif

#include "util/u_debug.h"
#include "util/u_time.h"
#include "util/u_var.h"
#include "utils/method.h"
#include <algorithm>
#include <cassert>
#include <cmath>
#include <ranges>

// Default value is half the compositor margin
DEBUG_GET_ONCE_FLOAT_OPTION(min_margin_ms, "U_PACING_APP_MIN_MARGIN_MS", 1.5f)

namespace wivrn
{

class app_pacer : public u_pacing_app
{
	pacing_app_factory & parent;
	int64_t frame_id = 0;
	int64_t compositor_display_time = 0;
	int64_t last_display_time = 0;
	int64_t period = 10'000'000;

	std::mutex mutex; // locks cpu/draw/gpu time
	int64_t cpu_time = 0;
	int64_t gpu_time = 0;
	int64_t compositor_time = 0;
	app_performance performance;
#ifdef WIVRN_CODEC_TESTS
	void record_source(codec_test::source_event event, int64_t id, int64_t when_ns,
	                   int64_t display_time_ns = 0, int64_t system_frame_id = -1,
	                   int64_t display_period_ns = 0, int64_t wake_up_ns = 0,
	                   uint32_t timing_point = 0)
	{
		auto & trace = codec_test::source_trace();
		if (trace.enabled())
			trace.append({.event = event, .pacer_id = performance.id,
			              .app_frame_id = id, .system_frame_id = system_frame_id,
			              .when_ns = when_ns, .observed_ns = int64_t(os_monotonic_get_ns()),
			              .display_time_ns = display_time_ns, .display_period_ns = display_period_ns,
			              .wake_up_ns = wake_up_ns, .timing_point = timing_point});
	}
#endif


	u_var_draggable_f32 min_margin_ms;

	struct frame
	{
		int64_t frame_id;
		int64_t wake_up;
		int64_t delivered;
	};
	std::array<frame, 16> frames{};

	frame & get_frame(int64_t id)
	{
		assert(id >= 0);
		return frames[id % frames.size()];
	}

public:
	using base_t = u_pacing_app;

	app_pacer(pacing_app_factory & parent, uint64_t id) :
	        u_pacing_app{
	                .predict = method_pointer<&app_pacer::predict>,
	                .mark_point = method_pointer<&app_pacer::mark_point>,
	                .mark_discarded = method_pointer<&app_pacer::mark_discarded>,
	                .mark_delivered = method_pointer<&app_pacer::mark_delivered>,
	                .mark_gpu_done = method_pointer<&app_pacer::mark_gpu_done>,
	                .latched = method_pointer<&app_pacer::latched>,
	                .retired = method_pointer<&app_pacer::retired>,
	                .info = method_pointer<&app_pacer::info>,
	                .destroy = method_pointer<&app_pacer::destroy>,
	        },
	        parent(parent),
	        min_margin_ms{
	                .val = debug_get_float_option_min_margin_ms(),
	                .step = 0.1,
	                .min = 0.5,
	                .max = 5.0,
	        }
	{
		performance.id = id;
		// U variable tracking.
		u_var_add_root(this, "App timing info", true);
		u_var_add_draggable_f32(this, &min_margin_ms, "Minimum margin(ms)");
	}

	~app_pacer()
	{
		u_var_remove_root(this);
	}

	void predict(int64_t now_ns,
	             int64_t * out_frame_id,
	             int64_t * out_wake_up_time,
	             int64_t * out_predicted_display_time,
	             int64_t * out_predicted_display_period);
	void mark_point(int64_t frame_id, enum u_timing_point point, int64_t when_ns);
	void mark_discarded(int64_t frame_id, int64_t when_ns);
	void mark_delivered(int64_t frame_id, int64_t when_ns, int64_t display_time_ns);
	void mark_gpu_done(int64_t frame_id, int64_t when_ns);
	void latched(int64_t frame_id, int64_t when_ns, int64_t system_frame_id);
	void retired(int64_t frame_id, int64_t when_ns);
	void info(int64_t predicted_display_time_ns,
	          int64_t predicted_display_period_ns,
	          int64_t extra_ns);

	int64_t get_app_time()
	{
		std::lock_guard lock(mutex);
		return std::max(cpu_time, gpu_time);
	}

	app_performance snapshot()
	{
		std::lock_guard lock(mutex);
		return performance;
	}

	void destroy()
	{
		parent.remove_app(this);
		delete this;
	}
};

void app_pacer::predict(int64_t now_ns,
                        int64_t * out_frame_id,
                        int64_t * out_wake_up_time,
                        int64_t * out_predicted_display_time,
                        int64_t * out_predicted_display_period)
{
	*out_frame_id = ++frame_id;
	get_frame(frame_id) = {.frame_id = frame_id};

	// no need to lock, called in same thread as writes
	auto min_ready = now_ns + cpu_time + gpu_time + compositor_time;
	// The ideal display time: one frame after the last
	last_display_time += period;
	// Sync phase with compositor
	last_display_time = compositor_display_time + period * ((period / 2 + last_display_time - compositor_display_time) / period);

	if (cpu_time > period or gpu_time > period or (min_ready > last_display_time and min_ready < last_display_time + period))
	{
		// We are limited by app time, don't wait
		*out_wake_up_time = now_ns;
		*out_predicted_display_period = period; // FIXME: may be more than one frame
		while (last_display_time < min_ready)
			last_display_time += period;
		*out_predicted_display_time = last_display_time;
	}
	else
	{
		// Ensure display time is achievable
		while (last_display_time < min_ready)
			last_display_time += period;

		*out_predicted_display_time = last_display_time;
		*out_predicted_display_period = period;
		*out_wake_up_time = last_display_time - (cpu_time + gpu_time + compositor_time + int64_t(U_TIME_1MS_IN_NS * min_margin_ms.val));
	}
#ifdef WIVRN_CODEC_TESTS
	record_source(codec_test::source_event::app_predict, *out_frame_id, now_ns,
	              *out_predicted_display_time, -1, *out_predicted_display_period,
	              *out_wake_up_time);
#endif
}

void app_pacer::mark_point(int64_t frame_id, enum u_timing_point point, int64_t when_ns)
{
#ifdef WIVRN_CODEC_TESTS
	record_source(codec_test::source_event::app_point, frame_id, when_ns, 0, -1, 0, 0, uint32_t(point));
#endif
	auto & frame = get_frame(frame_id);
	if (frame.frame_id != frame_id)
		return;
	switch (point)
	{
		case U_TIMING_POINT_WAKE_UP:
			frame.wake_up = when_ns;
			break;
		case U_TIMING_POINT_BEGIN:
		case U_TIMING_POINT_SUBMIT_BEGIN:
		case U_TIMING_POINT_SUBMIT_END:
			break;
	}
}

void app_pacer::mark_delivered(int64_t frame_id, int64_t when_ns, int64_t display_time_ns)
{
#ifdef WIVRN_CODEC_TESTS
	record_source(codec_test::source_event::app_delivered, frame_id, when_ns, display_time_ns);
#endif
	auto & frame = get_frame(frame_id);
	if (frame.frame_id != frame_id)
		return;
	frame.delivered = when_ns;
}

void app_pacer::mark_discarded(int64_t frame_id, int64_t when_ns)
{
#ifdef WIVRN_CODEC_TESTS
	record_source(codec_test::source_event::app_discarded, frame_id, when_ns);
#endif
	auto & frame = get_frame(frame_id);
	if (frame.frame_id != frame_id)
		return;
	frame.delivered = when_ns;
}

void app_pacer::latched(int64_t frame_id, int64_t when_ns, int64_t system_frame_id)
{
#ifdef WIVRN_CODEC_TESTS
	record_source(codec_test::source_event::app_latched, frame_id, when_ns, 0, system_frame_id);
#endif
}

void app_pacer::retired(int64_t frame_id, int64_t when_ns)
{
#ifdef WIVRN_CODEC_TESTS
	record_source(codec_test::source_event::app_retired, frame_id, when_ns);
#endif
}

void app_pacer::info(int64_t predicted_display_time_ns,
                     int64_t predicted_display_period_ns,
                     int64_t extra_ns)
{
	compositor_display_time = predicted_display_time_ns;
	period = predicted_display_period_ns;
	compositor_time = std::max<int64_t>(0, extra_ns);
}

int64_t lerp0(int64_t a, int64_t b, double t)
{
	if (a == 0)
		return b;
	return std::lerp(a, b, t);
}

void app_pacer::mark_gpu_done(int64_t frame_id, int64_t when_ns)
{
#ifdef WIVRN_CODEC_TESTS
	record_source(codec_test::source_event::app_gpu_done, frame_id, when_ns);
#endif
	auto & frame = get_frame(frame_id);
	if (frame.frame_id != frame_id or not(frame.wake_up and frame.delivered))
		return;

	std::lock_guard lock(mutex);
	cpu_time = lerp0(cpu_time, std::min(3 * period, frame.delivered - frame.wake_up), 0.1);
	gpu_time = lerp0(gpu_time, std::min(3 * period, when_ns - frame.delivered), 0.1);
	if (when_ns > performance.updated_ns)
	{
		if (performance.updated_ns && when_ns - performance.updated_ns < 1'000'000'000)
			performance.interval_ns = lerp0(performance.interval_ns, when_ns - performance.updated_ns, 0.1);
		else
			performance.interval_ns = 0;
		performance.updated_ns = when_ns;
		performance.completed++;
		performance.period_ns = period;
		performance.cpu_ns = std::max<int64_t>(0, cpu_time);
		performance.gpu_ns = std::max<int64_t>(0, gpu_time);
	}
}

pacing_app_factory::pacing_app_factory() :
        u_pacing_app_factory{
                .create = method_pointer<&pacing_app_factory::create>,
                .destroy = method_pointer<&pacing_app_factory::destroy>,
        }
{
#ifdef WIVRN_CODEC_TESTS
	(void)codec_test::source_trace();
#endif
}

void pacing_app_factory::remove_app(app_pacer * app)
{
	std::lock_guard lock(mutex);
	std::erase(app_pacers, app);
}

xrt_result_t pacing_app_factory::create(struct u_pacing_app ** out_upa)
{
	std::lock_guard lock(mutex);
	*out_upa = app_pacers.emplace_back(new app_pacer(*this, ++next_id));
	return XRT_SUCCESS;
}

void pacing_app_factory::destroy()
{
}

int64_t pacing_app_factory::get_frame_time()
{
	std::lock_guard lock(mutex);
	if (app_pacers.empty())
		return 1;
	return std::ranges::max(std::ranges::transform_view(app_pacers, [](auto * pacer) { return pacer->get_app_time(); }));
}

std::vector<app_performance> pacing_app_factory::performance_snapshot()
{
	std::lock_guard lock(mutex);
	std::vector<app_performance> result;
	for (auto * app: app_pacers)
	{
		if (result.size() == 32)
			break;
		result.push_back(app->snapshot());
	}
	return result;
}

} // namespace wivrn
