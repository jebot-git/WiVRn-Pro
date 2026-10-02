/*
 * WiVRn VR streaming
 * Copyright (C) 2025  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#pragma once

#include "util/u_pacing.h"

#include <mutex>
#include <vector>

namespace wivrn
{

class wivrn_session;
class app_pacer;

// Values are pacing estimates, not instrumented CPU/GPU execution times.
struct app_performance
{
	uint64_t id = 0, completed = 0;
	int64_t updated_ns = 0, period_ns = 0, interval_ns = 0, cpu_ns = 0, gpu_ns = 0;
};

class pacing_app_factory : public u_pacing_app_factory
{
	friend class app_pacer;
	std::mutex mutex;
	std::vector<app_pacer *> app_pacers;
	uint64_t next_id = 0;
	void remove_app(app_pacer *);

public:
	using base_t = u_pacing_app_factory;

	pacing_app_factory();
	xrt_result_t create(struct u_pacing_app ** out_upa);
	void destroy();

	int64_t get_frame_time();
	std::vector<app_performance> performance_snapshot();
};

} // namespace wivrn
