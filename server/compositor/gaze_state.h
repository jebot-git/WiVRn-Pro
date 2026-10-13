// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <bit>
#include <chrono>
#include <cstdint>
namespace wivrn
{
// Use receive-side monotonic time: headset and host clocks have different epochs.
template <typename Quaternion>
class gaze_state
{
	Quaternion orientation{};
	std::chrono::steady_clock::time_point received{};

public:
	void update(Quaternion value, bool tracked, std::chrono::steady_clock::time_point now)
	{
		auto finite = [](float f) { return (std::bit_cast<uint32_t>(f) & 0x7f800000u) != 0x7f800000u; };
		const float norm = value.x * value.x + value.y * value.y + value.z * value.z + value.w * value.w;
		if (!tracked || !finite(norm) || norm < 0.5f || norm > 1.5f)
		{
			orientation = {};
			received = {};
			return;
		}
		orientation = value;
		received = now;
	}
	Quaternion get(std::chrono::steady_clock::time_point now) const
	{
		return now - received <= std::chrono::milliseconds(100) ? orientation : Quaternion{};
	}
};
} // namespace wivrn
