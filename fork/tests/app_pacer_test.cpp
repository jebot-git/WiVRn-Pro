// SPDX-License-Identifier: GPL-3.0-or-later
#include "app_pacer.h"

#include <algorithm>
#include <cassert>
#include <iostream>

namespace
{
constexpr int64_t ms = 1'000'000;
constexpr int64_t compositor_margin = 68 * ms;

struct simulation
{
	wivrn::pacing_app_factory factory;
	u_pacing_app * app = nullptr;
	int64_t now = 1'000'000'000'000;
	int64_t period;
	int64_t last_display = 0;
	int64_t last_id = 0;
	int64_t max_lead = 0;
	unsigned waits = 0;

	explicit simulation(int64_t period) : period(period)
	{
		assert(factory.create(&app) == XRT_SUCCESS);
	}
	~simulation()
	{
		app->destroy(app);
	}

	void frame(int64_t cpu, int64_t gpu, bool discard, bool update_compositor = true)
	{
		if (update_compositor)
			app->info(app, (now / period + 1) * period + compositor_margin, period, compositor_margin);
		int64_t id, wake, display, predicted_period;
		app->predict(app, now, &id, &wake, &display, &predicted_period);
		assert(id > last_id && display > last_display && predicted_period == period);
		last_id = id;
		last_display = display;
		if (wake > now)
			++waits;
		now = std::max(now, wake); // xrWaitFrame honours the requested wake time.
		max_lead = std::max(max_lead, display - now);
		app->mark_point(app, id, U_TIMING_POINT_WAKE_UP, now);
		now += cpu;
		if (discard)
			app->mark_discarded(app, id, now); // xrEndFrame with no layers.
		else
		{
			app->mark_delivered(app, id, now, display);
			now += gpu;
			app->mark_gpu_done(app, id, now);
		}
	}
};

void hidden_then_visible(int64_t period, int64_t cpu, int64_t gpu)
{
	simulation s(period);
	// Establish a real slow-frame estimate, as under VR application load.
	for (int n = 0; n < 120; ++n)
		s.frame(cpu, gpu, false);
	s.max_lead = 0;
	s.waits = 0;
	// No GPU completion is reported for empty frames. The old estimate stays
	// above one refresh period even though the application now runs quickly.
	for (int n = 0; n < 100'000; ++n)
		s.frame(ms, 0, true);
	std::cout << "empty frames at " << 1e9 / period << " Hz: max lead=" << s.max_lead / 1e6
	          << " ms, paced waits=" << s.waits << std::endl;
	assert(s.max_lead <= cpu + gpu + compositor_margin + 3 * period);
	assert(s.waits > 0);
	// First layer submission must be displayable promptly, without a queue
	// slot remaining occupied until a timestamp minutes into the future.
	int64_t resumed_at = s.now;
	s.frame(ms, ms, false);
	assert(s.last_display - resumed_at < cpu + gpu + compositor_margin + 4 * period);
	// The estimator must converge again once real GPU completions resume.
	for (int n = 0; n < 200; ++n)
		s.frame(ms, ms, false);
	auto stats = s.factory.performance_snapshot();
	assert(stats.size() == 1 && stats[0].completed == 321);
	assert(stats[0].cpu_ns < 2 * ms && stats[0].gpu_ns < 2 * ms);
}
} // namespace

int main()
{
	for (int hz: {72, 80, 90, 120})
	{
		int64_t period = 1'000'000'000 / hz;
		hidden_then_visible(period, period + 5 * ms, 2 * ms);
		hidden_then_visible(period, ms, period + 5 * ms);
		hidden_then_visible(period, ms, ms);
	}
	// Replay the CPU/GPU estimates captured from the stalled Dome session.
	hidden_then_visible(11'111'111, 16'141'368, 2'522'821);
	// Slow submitted frames should still run immediately when their real work
	// keeps pace with predictions: the fix must not force half-refresh rate.
	simulation slow(1'000'000'000 / 90);
	for (int n = 0; n < 120; ++n)
		slow.frame(16 * ms, 2 * ms, false);
	slow.waits = 0;
	for (int n = 0; n < 100; ++n)
		slow.frame(16 * ms, 2 * ms, false);
	assert(slow.waits == 0);
	// A stopped compositor timing feed must not allow runaway predictions.
	for (int n = 0; n < 10'000; ++n)
		slow.frame(ms, 0, true, false);
	assert(slow.last_display - slow.now < 120 * ms);
	// Recover after suspension and a change of refresh period.
	slow.now += 300'000 * ms;
	slow.period = 1'000'000'000 / 72;
	for (int n = 0; n < 200; ++n)
		slow.frame(ms, ms, false);
	assert(slow.last_display > slow.now && slow.last_display - slow.now < 120 * ms);
	std::cout << "PASS actual app_pacer callbacks: empty-frame regression, CPU/GPU load, cadence, stale timing, resume, refresh changes\n";
}
