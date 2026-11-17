// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "xrt/xrt_defines.h"
#include <bit>
#include <cstdint>
#include <optional>

namespace wivrn
{
// Visual continuity only: held poses stay valid but are never reported tracked
// and never retain prediction velocities. Input freshness remains independent.
class held_pose
{
	std::optional<xrt_pose> last;
	int64_t last_timestamp = 0;

public:
	void reset()
	{
		last.reset();
		last_timestamp = 0;
	}
	xrt_space_relation apply(xrt_space_relation relation, bool tracked, int64_t timestamp)
	{
		constexpr int valid = XRT_SPACE_RELATION_POSITION_VALID_BIT | XRT_SPACE_RELATION_ORIENTATION_VALID_BIT;
		const auto & p = relation.pose.position;
		const auto & q = relation.pose.orientation;
		// The server uses fast-math. Inspect exponent bits so invalid network
		// values cannot be accepted through an optimized-away isfinite check.
		const auto finite = [](float v) { return (std::bit_cast<uint32_t>(v) & 0x7f800000u) != 0x7f800000u; };
		const bool valid_components = finite(p.x) && finite(p.y) && finite(p.z) && finite(q.x) && finite(q.y) && finite(q.z) && finite(q.w);
		const float norm = q.x * q.x + q.y * q.y + q.z * q.z + q.w * q.w;
		if (tracked && (relation.relation_flags & valid) == valid && valid_components && norm >= 0.5f && norm <= 1.5f)
		{
			if (!last || timestamp >= last_timestamp)
			{
				last = relation.pose;
				last_timestamp = timestamp;
			}
			return relation;
		}
		if (last)
			return {.relation_flags = xrt_space_relation_flags(valid), .pose = *last, .linear_velocity = {}, .angular_velocity = {}};
		// No real pose has arrived yet. Do not publish a valid neutral pose.
		return {.relation_flags = xrt_space_relation_flags(0), .pose = XRT_POSE_IDENTITY, .linear_velocity = {}, .angular_velocity = {}};
	}
};
} // namespace wivrn
