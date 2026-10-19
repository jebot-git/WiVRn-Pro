// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdint>

namespace wivrn
{
// OpenXR joint order. Kept independent of the transport and runtime for testing.
class hand_controller_gestures
{
public:
	struct point
	{
		float x, y, z;
	};
	struct value
	{
		float trigger = 0, grip = 0;
		bool trigger_click = false, first = false, second = false, grip_click = false;
	};

private:
	std::array<point, 26> joints{};
	int64_t timestamp = 0;
	bool valid = false;
	std::array<bool, 4> armed{}, pressed{};
	static float distance(point a, point b)
	{
		return std::hypot(a.x - b.x, a.y - b.y, a.z - b.z);
	}

public:
	void reset()
	{
		armed = {};
		pressed = {};
	}
	void sample(const std::array<point, 26> & points, bool tracked, int64_t time)
	{
		if (time <= timestamp)
			return;
		if (time - timestamp > 150'000'000 || !tracked)
			reset();
		timestamp = time;
		joints = points;
		valid = tracked;
		for (auto p: points)
			valid &= std::isfinite(p.x) && std::isfinite(p.y) && std::isfinite(p.z);
	}
	value update(int64_t now, bool enabled, bool suppressed)
	{
		if (!enabled || suppressed || !valid || timestamp <= 0 || now < timestamp || now - timestamp > 150'000'000)
		{
			reset();
			return {};
		}
		float span = distance(joints[7], joints[22]);
		if (span < .03f || span > .13f)
		{
			reset();
			return {};
		}
		std::array<float, 4> amount{};
		for (unsigned i = 0; i < 3; i++)
			amount[i] = std::clamp((.5f - distance(joints[5], joints[10 + 5 * i]) / span) / .28f, 0.f, 1.f);
		// All three lower fingers must curl: a middle/ring pinch alone is not a grip.
		amount[3] = 1;
		for (unsigned base: {12u, 17u, 22u})
		{
			float length = 0;
			for (unsigned i = base; i < base + 3; i++)
				length += distance(joints[i], joints[i + 1]);
			if (length < .025f || length > .20f)
			{
				reset();
				return {};
			}
			amount[3] = std::min(amount[3], std::clamp((.85f - distance(joints[base], joints[base + 3]) / length) / .5f, 0.f, 1.f));
		}
		// Closing a fist must not manufacture face-button pinches.
		if (amount[3] > .5f)
			for (unsigned i = 0; i < 3; i++)
			{
				amount[i] = 0;
				armed[i] = false;
			}
		for (unsigned i = 0; i < 4; i++)
		{
			if (i < 3 && amount[3] > .5f)
			{
				armed[i] = false;
				pressed[i] = false;
				continue;
			}
			if (amount[i] < .2f)
				armed[i] = true;
			if (!armed[i])
				amount[i] = 0;
			pressed[i] = armed[i] && amount[i] > (pressed[i] ? .4f : .75f);
		}
		return {amount[0], amount[3], pressed[0], pressed[1], pressed[2], pressed[3]};
	}
};
} // namespace wivrn
