// SPDX-License-Identifier: GPL-3.0-or-later
#include "driver/refresh_rate_policy.h"
#include <array>
#include <cassert>
#include <iostream>

int main()
{
	using wivrn::select_refresh_rate;
	const std::array rates{72.f, 80.f, 90.f};
	float previous = 90;
	// Replay the measured application rates that made the live session bounce
	// 90 -> 72 -> 90 -> 72 -> 90 every ten seconds after decoder recovery.
	for (float app_rate: {70.8f, 81.7f, 63.9f, 80.8f, 48.1f})
	{
		previous = select_refresh_rate(rates, 0, app_rate, previous);
		assert(previous == 72);
	}
	// Minimums are inclusive, and even an unreachable minimum must only choose
	// a supported rate. Do not depend on the runtime's enumeration order.
	assert(select_refresh_rate(rates, 72, 30, 90) == 72);
	assert(select_refresh_rate(rates, 80, 30, 90) == 80);
	assert(select_refresh_rate(rates, 100, 30, 90) == 90);
	assert(select_refresh_rate(std::array{90.f, 72.f, 80.f}, 0, 30, 90) == 72);
	// Recovery of application performance must still allow raising the rate;
	// retain headroom for switching and tolerate smaller fluctuations in place.
	assert(select_refresh_rate(rates, 0, 88, 72) == 72);
	assert(select_refresh_rate(rates, 0, 90, 72) == 80);
	assert(select_refresh_rate(rates, 0, 101, 80) == 90);
	assert(select_refresh_rate(rates, 0, 95, 90) == 90);
	assert(select_refresh_rate(std::array{80.f}, 0, 30, 0) == 80);
	std::cout << "PASS refresh-rate regression replay, minimums, enumeration order and headroom\n";
}
