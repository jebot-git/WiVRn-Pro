/*
 * WiVRn VR streaming
 * Copyright (C) 2026  WiVRn Contributors
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

// Monado's vk_helpers.h pulls in xrt_vulkan_includes.h, which sets the
// VK_USE_PLATFORM_* defines *before* <vulkan/vulkan.h> gets included for the
// first time. Including it before wivrn_vk_bundle.h (which transitively pulls
// in vulkan_raii.hpp) ensures the platform-specific PFN_* surface types are
// declared and the function-pointer table in vk_helpers_h_funcs.h.inc compiles.
#ifdef WIVRN_USE_PERFETTO
#include "vk/vk_helpers.h"
#endif

#include "gpu_timestamp_pool.h"

#include "performance.h"
#include "wivrn_trace.h"
#include "wivrn_vk_bundle.h"
#include <array>

namespace wivrn
{
// Timestamp allocation is opt-in through tracing or lightweight profiling.
gpu_timestamp_pool::gpu_timestamp_pool(wivrn::vk_bundle & vk,
                                       uint32_t queue_family_index,
                                       uint32_t slot_count,
                                       const std::string & name)
{
	if (!wivrn::trace::is_enabled() && !profile_enabled())
		return;
	auto qprops = vk.physical_device.getQueueFamilyProperties();
	if (queue_family_index >= qprops.size() or qprops[queue_family_index].timestampValidBits == 0)
		return;

	pool = vk.device.createQueryPool(vk::QueryPoolCreateInfo{
	        .queryType = vk::QueryType::eTimestamp,
	        .queryCount = slot_count * 2,
	});
	vk.name(pool, name);
	frame_indices.assign(slot_count, 0);
	timestamp_period = vk.physical_device.getProperties().limits.timestampPeriod;
	timestamp_bits = qprops[queue_family_index].timestampValidBits;
}

void gpu_timestamp_pool::cmd_begin(const vk::raii::CommandBuffer & cmd, uint8_t slot, uint64_t frame_index, vk::PipelineStageFlagBits2 stage)
{
	if (not*pool)
		return;
	cmd.resetQueryPool(*pool, slot * 2, 2);
	cmd.writeTimestamp2(stage, *pool, slot * 2);
	frame_indices[slot] = frame_index;
}

void gpu_timestamp_pool::cmd_end(const vk::raii::CommandBuffer & cmd, uint8_t slot, vk::PipelineStageFlagBits2 stage) const
{
	if (not*pool)
		return;
	cmd.writeTimestamp2(stage, *pool, slot * 2 + 1);
}

std::optional<gpu_timestamp_pool::sample> gpu_timestamp_pool::collect(uint8_t slot)
{
	if (not*pool)
		return std::nullopt;
	auto [res, times] = pool.getResult<std::array<uint64_t, 2>>(slot * 2, 2, sizeof(uint64_t), vk::QueryResultFlagBits::e64 | vk::QueryResultFlagBits::eWait);
	if (res != vk::Result::eSuccess)
		return std::nullopt;
	const uint64_t mask = timestamp_bits == 64 ? ~uint64_t{0} : (uint64_t{1} << timestamp_bits) - 1;
	const double duration = ((times[1] - times[0]) & mask) * timestamp_period;
	int64_t begin = 0, end = 0;
#ifdef WIVRN_USE_PERFETTO
	if (auto cal = wivrn::trace::calibration_source(); cal && trace::is_enabled())
		if (vk_convert_timestamps_to_host_ns(cal, times.size(), times.data()) == VK_SUCCESS)
		{
			begin = times[0];
			end = times[1];
		}
#endif
	return sample{duration, begin, end, frame_indices[slot]};
}
} // namespace wivrn
