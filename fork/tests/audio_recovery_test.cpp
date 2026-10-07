// SPDX-License-Identifier: GPL-3.0-or-later
#include "utils/audio_recovery.h"
#include <cassert>
#include <iostream>
using namespace std::chrono_literals;
int main()
{
	using clock = std::chrono::steady_clock;
	auto t = clock::time_point{};
	utils::microphone_session_policy policy{true, true, true, false, true, false};
	assert(policy.capture() && policy.recover() && !policy.forward());
	// Repeated reader changes during world loads must never close capture.
	for (int i = 0; i < 100; ++i)
	{
		policy.reader_requested = i % 2;
		assert(policy.capture() && policy.recover());
		assert(policy.forward() == policy.reader_requested);
	}
	policy.user_present = false;
	assert(policy.capture() && !policy.forward() && !policy.recover());
	policy.user_present = true;
	policy.system_blocked = true;
	assert(!policy.capture() && !policy.forward() && !policy.recover());
	policy.system_blocked = false;
	policy.capture_enabled = false;
	assert(!policy.capture() && !policy.forward() && !policy.recover());
	policy.capture_enabled = true;
	policy.session_visible = false;
	assert(!policy.capture() && !policy.forward() && !policy.recover());
	policy.session_visible = true;
	assert(policy.capture() && policy.forward() && policy.recover());
	policy.device_available = false;
	assert(!policy.capture() && !policy.forward() && !policy.recover());
	utils::microphone_watchdog w;
	w.start(t, 0);
	assert(!w.stalled(t + 2s, 0, {})); // startup grace
	assert(w.stalled(t + 3s, 0, {}));  // missing callbacks
	assert(w.retry(t + 3s));
	assert(!w.retry(t + 3500ms)); // backoff
	assert(w.retry(t + 4s));
	assert(w.retry(t + 6s));
	assert(!w.retry(t + 35s)); // exhausted burst enters slow recovery
	assert(w.retry(t + 36s)); // transient HAL failure must not permanently disable capture
	assert(!w.retry(t + 60s));
	assert(w.attempts == 3); // saturates, avoiding retry-counter overflow
	w.start(t + 61s, 0);
	for (int i = 1; i <= 12; ++i)
		assert(!w.stalled(t + 61s + i * 1s, i * 48000, {}));
	assert(w.attempts == 3); // unsupported timestamps cannot rearm synthetic capture
	w.start(t + 80s, 0);
	assert(!w.stalled(t + 81s, 48000, 48000));
	assert(!w.stalled(t + 83s, 144000, 48000));
	assert(w.stalled(t + 84s, 192000, 48000)); // callbacks continue, hardware frozen
	w.start(t + 90s, 0);
	assert(!w.stalled(t + 91s, 48000, 48000));
	assert(w.stalled(t + 94s, 192000, {})); // previously valid timestamp lost
	w.start(t + 100s, 0);
	for (int i = 1; i <= 12; ++i)
		assert(!w.stalled(t + 100s + i * 1s, i * 48000, i * 48000));
	assert(w.attempts == 0); // sustained hardware progress rearms budget
	// Signal amplitude is intentionally absent: quiet, progressing capture is healthy.
	w = {};
	w.start(t, 0, 48000);
	assert(!w.stalled(t + 1s, 14000, 14000));
	assert(!w.stalled(t + 2s, 28000, 28000));
	assert(w.stalled(t + 3s, 42000, 42000)); // synthetic timestamps, severely under-rate delivery
	assert(w.retry(t + 3s));
	w.start(t + 4s, 42000, 48000);
	for (int i = 1; i <= 12; ++i)
		assert(!w.stalled(t + 4s + i * 1s, 42000 + i * 48000, 42000 + i * 48000));
	assert(w.attempts == 0);
	// Reproduce the captured resume failure: callbacks and timestamps advance
	// at only ~14 kHz on a 48 kHz stream, including after three failed reopens.
	w = {};
	uint64_t frames = 0;
	int reopens = 0;
	w.start(t, frames, 48000);
	for (int second = 1; second <= 120; ++second)
	{
		frames += 14000;
		auto now = t + second * 1s;
		if (w.stalled(now, frames, frames) && w.retry(now))
		{
			++reopens;
			w.start(now, frames, 48000);
		}
	}
	assert(reopens == 6); // three initial retries, then 39, 69, 99 seconds
	for (int second = 121; second <= 135; ++second)
	{
		frames += 48000;
		w.stalled(t + second * 1s, frames, frames);
	}
	assert(w.attempts == 0); // hardware recovers without restarting the app
	w = {};
	assert(w.retry(t));
	assert(w.retry(t + 1s));
	assert(w.retry(t + 3s)); // cooldown would normally end at 33 seconds
	w.start(t + 4s, 0, 48000);
	for (int second = 1; second <= 12; ++second)
		assert(!w.stalled(t + 4s + second * 1s, second * 48000, second * 48000));
	assert(w.attempts == 0 && w.can_retry(t + 16s)); // sustained recovery cancels cooldown
	utils::microphone_reopen_delay delay;
	delay.defer(t);
	assert(!delay.ready(t + 999ms));
	assert(delay.ready(t + 1s));
	// Another lifecycle interruption restarts the settling interval.
	delay.defer(t + 500ms);
	assert(!delay.ready(t + 1s));
	assert(delay.ready(t + 1500ms));
	std::cout << "Session capture and resume recovery tests passed\n";
}
