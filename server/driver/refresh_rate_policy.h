// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <algorithm>
#include <cassert>
#include <span>

namespace wivrn
{
inline float select_refresh_rate(std::span<const float> rates, float minimum, float app_rate, float previous)
{
	assert(!rates.empty());
	// If the application cannot sustain any rate, use the lowest permitted one.
	// Falling back to the maximum makes a slow application oscillate between
	// low and high rates, rebuilding QSV streams on every transition.
	float fallback = *std::max_element(rates.begin(), rates.end());
	float reachable = 0;
	for (float rate: rates)
	{
		if (rate < minimum)
			continue;
		fallback = std::min(fallback, rate);
		// Preserve the existing headroom requirement when changing rates.
		if (rate < app_rate * (rate == previous ? 1.f : 0.9f))
			reachable = std::max(reachable, rate);
	}
	return reachable != 0 ? reachable : fallback;
}
} // namespace wivrn
