// SPDX-License-Identifier: GPL-3.0-or-later
#include "held_pose.h"
#include <cassert>
#include <limits>

int main()
{
	using namespace wivrn;
	held_pose held;
	xrt_space_relation sample{
	        .relation_flags = xrt_space_relation_flags(XRT_SPACE_RELATION_POSITION_VALID_BIT | XRT_SPACE_RELATION_ORIENTATION_VALID_BIT |
	                                                  XRT_SPACE_RELATION_POSITION_TRACKED_BIT | XRT_SPACE_RELATION_ORIENTATION_TRACKED_BIT |
	                                                  XRT_SPACE_RELATION_LINEAR_VELOCITY_VALID_BIT | XRT_SPACE_RELATION_ANGULAR_VELOCITY_VALID_BIT),
	        .pose = {.orientation = {0, 0, 0, 1}, .position = {1, 2, 3}},
	        .linear_velocity = {4, 5, 6},
	        .angular_velocity = {1, 2, 3},
	};
	auto first = held.apply({}, false, 1);
	assert(first.relation_flags == 0); // Never tracked: no valid pose at the origin.
	assert(held.apply(sample, true, 2).pose.position.x == 1);
	constexpr int valid = XRT_SPACE_RELATION_POSITION_VALID_BIT | XRT_SPACE_RELATION_ORIENTATION_VALID_BIT;
	for (auto time: {3, 100, 1'000'000})
	{
		auto frozen = held.apply({}, false, time);
		assert(frozen.pose.position.x == 1 && frozen.pose.position.y == 2 && frozen.pose.position.z == 3);
		assert(frozen.pose.orientation.w == 1);
		assert(frozen.relation_flags == valid);
		assert(frozen.linear_velocity.x == 0 && frozen.angular_velocity.z == 0);
	}
	// Position-invalid/orientation-valid input must not move the held pose.
	auto partial = sample;
	partial.pose.position = {};
	partial.relation_flags = XRT_SPACE_RELATION_ORIENTATION_VALID_BIT;
	assert(held.apply(partial, true, 5).pose.position.x == 1);
	// Reacquisition resumes immediately; reordered queries cannot rewind the hold.
	sample.pose.position.x = 7;
	assert(held.apply(sample, true, 10).pose.position.x == 7);
	partial = sample;
	partial.pose.position.x = 4;
	held.apply(partial, true, 8);
	assert(held.apply({}, false, 11).pose.position.x == 7);
	partial.pose.position.x = std::numeric_limits<float>::quiet_NaN();
	assert(held.apply(partial, true, 12).pose.position.x == 7);
	partial = sample;
	partial.pose.orientation.w = 0;
	assert(held.apply(partial, true, 13).pose.position.x == 7);
	// Explicit stream/reference reset clears visual continuity.
	held.reset();
	assert(held.apply({}, false, 20).relation_flags == 0);
}
