// SPDX-License-Identifier: GPL-3.0-or-later
#include "audio/software_microphone.h"
#include <algorithm>
#include <cmath>
#include <condition_variable>
#include <iostream>
#include <mutex>
#include <random>
#include <stdexcept>
#include <vector>

using namespace std::chrono_literals;

void check(bool value, const char * message)
{
	if (not value)
		throw std::runtime_error(message);
}

struct collector
{
	std::mutex mutex;
	std::condition_variable changed;
	std::vector<int16_t> samples;
	std::vector<int64_t> times;

	void send(std::span<int16_t> data, int64_t timestamp)
	{
		std::lock_guard lock(mutex);
		samples.insert(samples.end(), data.begin(), data.end());
		times.push_back(timestamp);
		changed.notify_all();
	}
	void wait(size_t count)
	{
		std::unique_lock lock(mutex);
		check(changed.wait_for(lock, 2s, [&] { return samples.size() >= count; }), "worker output timed out");
	}
	size_t size()
	{
		std::lock_guard lock(mutex);
		return samples.size();
	}
};

void raw_passthrough()
{
	collector c;
	wivrn::software_microphone mic(48000, false, [&](auto data, auto time) { c.send(data, time); });
	std::vector<int16_t> input(1237);
	for (size_t i = 0; i < input.size(); ++i)
		input[i] = int16_t(i * 51);
	auto original = input;
	mic.set_enabled(true);
	mic.enqueue(input, 1000);
	c.wait(input.size());
	check(c.samples == original, "raw path changed or lost samples");
	check(input == original, "callback input was mutated");
	check(std::is_sorted(c.times.begin(), c.times.end()), "packet timestamps went backwards");
	check(not mic.failed(), "raw forwarding failed");
}

void fragmented_frames_and_pause()
{
	collector c;
	wivrn::software_microphone mic(48000, true, [&](auto data, auto time) { c.send(data, time); });
	mic.set_enabled(true);
	std::vector<int16_t> silence(480);
	mic.enqueue(std::span(silence).first(123), 0);
	mic.enqueue(std::span(silence).subspan(123), 123 * 1'000'000'000LL / 48000);
	c.wait(480);
	check(std::all_of(c.samples.begin(), c.samples.end(), [](auto x) { return x == 0; }), "silence became audio");
	std::vector<int16_t> old_partial(123, 30000);
	mic.enqueue(old_partial, 10'000'000);
	mic.set_enabled(false);
	mic.set_enabled(true);
	mic.enqueue(silence, 30'000'000);
	c.wait(960);
	check(c.samples.size() == 960, "partial frames leaked across pause");
	check(std::all_of(c.samples.begin() + 480, c.samples.end(), [](auto x) { return x == 0; }), "old speech leaked after restart");
	check(c.times.back() == 30'000'000, "partial-frame timestamp survived restart");
}

void queue_overflow_and_epoch()
{
	collector c;
	std::mutex gate_mutex;
	std::condition_variable gate;
	bool first = true, entered = false, release = false;
	wivrn::software_microphone mic(48000, false, [&](auto data, auto timestamp) {
		{
			std::unique_lock lock(gate_mutex);
			if (first)
			{
				first = false;
				entered = true;
				gate.notify_all();
				gate.wait_for(lock, 1s, [&] { return release; });
			}
		}
		c.send(data, timestamp);
	});
	mic.set_enabled(true);
	std::vector<int16_t> initial(480, 1), stale(480, 2), fresh(480, 3);
	mic.enqueue(initial, 0);
	{
		std::unique_lock lock(gate_mutex);
		check(gate.wait_for(lock, 1s, [&] { return entered; }), "worker did not enter sender");
	}
	for (int i = 0; i < 30; ++i)
		mic.enqueue(stale, i * 10'000'000LL);
	check(mic.dropped() > 0, "bounded queue failed to report overflow");
	mic.set_enabled(false);
	mic.set_enabled(true);
	{
		std::lock_guard lock(gate_mutex);
		release = true;
		gate.notify_all();
	}
	c.wait(480);
	// Wait for stale queue entries to drain without asserting scheduling speed.
	for (int i = 0; i < 20 and c.size() < 960; ++i)
	{
		mic.enqueue(fresh, 1'000'000'000);
		std::this_thread::sleep_for(2ms);
	}
	c.wait(960);
	std::lock_guard lock(c.mutex);
	check(std::none_of(c.samples.begin(), c.samples.end(), [](auto x) { return x == 2; }), "stale queued audio survived epoch change");
}

void denoise_and_benchmark()
{
	collector c;
	wivrn::software_microphone mic(48000, true, [&](auto data, auto time) { c.send(data, time); });
	mic.set_enabled(true);
	std::mt19937 random(42);
	std::uniform_int_distribution<int> noise(-1800, 1800);
	std::vector<int16_t> frame(480);
	double noise_energy = 0, tone_energy = 0;
	auto began = std::chrono::steady_clock::now();
	for (int f = 0; f < 300; ++f)
	{
		for (int i = 0; i < 480; ++i)
		{
			double tone = f >= 200 ? 7000 * std::sin(2 * 3.141592653589793 * 440 * (f * 480 + i) / 48000) : 0;
			frame[i] = int16_t(noise(random) + tone);
			if (f >= 150 and f < 200)
				noise_energy += double(frame[i]) * frame[i];
			if (f >= 250)
				tone_energy += double(frame[i]) * frame[i];
		}
		mic.enqueue(frame, int64_t(f) * 10'000'000);
		c.wait(size_t(f + 1) * 480);
	}
	double reduced_noise = 0, retained_tone = 0;
	for (size_t i = 150 * 480; i < 200 * 480; ++i)
		reduced_noise += double(c.samples[i]) * c.samples[i];
	for (size_t i = 250 * 480; i < 300 * 480; ++i)
		retained_tone += double(c.samples[i]) * c.samples[i];
	auto elapsed = std::chrono::duration<double>(std::chrono::steady_clock::now() - began).count();
	std::cout << "Noise power ratio=" << reduced_noise / noise_energy << ", tone power ratio="
	          << retained_tone / tone_energy << ", 3 seconds PCM processed in " << elapsed << " seconds\n";
	check(reduced_noise / noise_energy < .65, "stationary noise not reduced");
	check(retained_tone / tone_energy > .2, "foreground signal excessively suppressed");
	check(not mic.failed() and mic.dropped() == 0, "DSP pipeline lost frames");
}

void sender_failure_and_retry()
{
	collector c;
	std::atomic<bool> throw_once = true;
	wivrn::software_microphone mic(44100, false, [&](auto data, auto time) {
		if (throw_once.exchange(false))
			throw std::runtime_error("simulated disconnect");
		c.send(data, time);
	});
	std::vector<int16_t> frame(441, 12);
	mic.set_enabled(true);
	mic.enqueue(frame, 0);
	for (int i = 0; i < 100 and not mic.failed(); ++i)
		std::this_thread::sleep_for(1ms);
	check(mic.failed(), "send failure not surfaced to capture");
	mic.set_enabled(true); // explicitly retry a failed forwarding epoch
	mic.enqueue(frame, 10'000'000);
	c.wait(441);
	check(not mic.failed(), "worker could not resume after send failure");
}

int main()
{
	try
	{
		raw_passthrough();
		fragmented_frames_and_pause();
		queue_overflow_and_epoch();
		denoise_and_benchmark();
		sender_failure_and_retry();
		std::cout << "All microphone DSP/lifecycle tests passed\n";
	}
	catch (const std::exception & e)
	{
		std::cerr << e.what() << '\n';
		return 1;
	}
}
