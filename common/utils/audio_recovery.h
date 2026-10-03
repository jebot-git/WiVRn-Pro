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
	static constexpr auto stall_timeout = std::chrono::seconds(3);
	void start(clock::time_point now, uint64_t frames)
	{
		callback_progress = timestamp_progress = healthy_since = now;
		callbacks = frames;
		position.reset();
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
		bool bad = now - callback_progress >= stall_timeout || (position && now - timestamp_progress >= stall_timeout);
		if (bad)
			healthy_since = now;
		// Never rearm from synthetic callbacks alone: require sustained hardware progress.
		if (!bad && position && now - healthy_since >= std::chrono::seconds(10))
			attempts = 0;
		return bad;
	}
	bool retry(clock::time_point now)
	{
		if (attempts >= 3 || now < next_retry)
			return false;
		next_retry = now + std::chrono::seconds(1 << attempts++);
		return true;
	}
};
} // namespace utils
