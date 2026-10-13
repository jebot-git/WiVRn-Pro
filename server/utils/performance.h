// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "util/u_logging.h"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <string_view>
namespace wivrn
{
inline bool profile_enabled()
{
	static const bool enabled = [] { auto value = std::getenv("WIVRN_PROFILE"); return value && std::string_view(value) == "1"; }();
	return enabled;
}
// Per-owner rolling samples, no locks or allocations in the frame path.
class latency_samples
{
	std::array<double, 300> values{};
	size_t count = 0;

public:
	void add(double ms, const char * stage, int stream = -1)
	{
		if (!profile_enabled())
			return;
		values[count++] = ms;
		if (count != values.size())
			return;
		std::sort(values.begin(), values.end());
		U_LOG_I("Performance %s stream=%d n=%zu p50=%.3f p95=%.3f max=%.3f ms",
		        stage,
		        stream,
		        count,
		        values[149],
		        values[284],
		        values.back());
		count = 0;
	}
};
using performance_clock = std::chrono::steady_clock;
inline double elapsed_ms(performance_clock::time_point start)
{
	return std::chrono::duration<double, std::milli>(performance_clock::now() - start).count();
}
} // namespace wivrn
