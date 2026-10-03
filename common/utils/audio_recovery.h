// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <chrono>
#include <cstdint>
#include <optional>
namespace utils
{
struct microphone_idle_hold
{
	using clock = std::chrono::steady_clock;
	static constexpr auto grace = std::chrono::seconds(30);
	bool enabled = false;
	std::optional<clock::time_point> deadline;
	bool update(bool reader, bool idle_allowed, clock::time_point now)
	{
		if (reader)
		{
			enabled = true;
			deadline.reset();
		}
		else if (!idle_allowed)
		{
			enabled = false;
			deadline.reset();
		}
		else if (enabled)
		{
			if (!deadline)
				deadline = now + grace;
			if (now >= *deadline)
			{
				enabled = false;
				deadline.reset();
			}
		}
		return enabled;
	}
};
struct microphone_watchdog
{
	using clock = std::chrono::steady_clock;
	clock::time_point callback_progress{}, timestamp_progress{}, healthy_since{}, next_retry{};
	uint64_t callbacks = 0;
	std::optional<int64_t> position;
	unsigned attempts = 0;
	int32_t sample_rate = 0;
	clock::time_point cadence_start{};
	uint64_t cadence_frames = 0;
	bool starved = false;
	static constexpr auto stall_timeout = std::chrono::seconds(3);
	void start(clock::time_point now, uint64_t frames, int32_t rate = 0)
	{
		callback_progress = timestamp_progress = healthy_since = now;
		callbacks = frames;
		position.reset();
		sample_rate = rate;
		cadence_start = now;
		cadence_frames = frames;
		starved = false;
	}
	bool stalled(clock::time_point now, uint64_t frames, std::optional<int64_t> hardware_position)
	{
		if (frames != callbacks)
		{
			callbacks = frames;
			callback_progress = now;
		}
		if (hardware_position && (!position || *hardware_position > *position))
		{
			if (!position)
				healthy_since = now;
			position = hardware_position;
			timestamp_progress = now;
		}
		if (now - cadence_start >= stall_timeout)
		{
			// Quest HAL contention can advance synthetic timestamps at ~14 kHz
			// on a 48 kHz stream. Progress alone cannot establish healthy capture.
			double expected = sample_rate * std::chrono::duration<double>(now - cadence_start).count();
			starved = sample_rate > 0 && double(frames - cadence_frames) < expected * 0.5;
			cadence_start = now;
			cadence_frames = frames;
		}
		bool bad = starved || now - callback_progress >= stall_timeout || (position && now - timestamp_progress >= stall_timeout);
		if (bad)
			healthy_since = now;
		// Never rearm from synthetic callbacks alone: require sustained hardware progress.
		if (!bad && position && now - healthy_since >= std::chrono::seconds(10))
			attempts = 0;
		return bad;
	}
	bool can_retry(clock::time_point now) const
	{
		return attempts < 3 && now >= next_retry;
	}
	bool retry(clock::time_point now)
	{
		if (!can_retry(now))
			return false;
		next_retry = now + std::chrono::seconds(1 << attempts++);
		return true;
	}
};
} // namespace utils
