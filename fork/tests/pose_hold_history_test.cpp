// SPDX-License-Identifier: GPL-3.0-or-later
#include "driver/pose_list.h"
#include "driver/clock_offset.h"
#include "os/os_time.h"
#include <cassert>
#include <cmath>
#include <thread>

XrTime wivrn::clock_offset::from_headset(XrTime timestamp) const
{
	assert(stable && b == 0);
	return timestamp;
}

int main()
{
	using namespace wivrn;
	pose_list grip(device_id::LEFT_GRIP, true);
	clock_offset offset{.b = 0, .stable = true};
	from_headset::tracking packet{};
	packet.device_poses.push_back({
	        .pose = {.orientation = {0, 0, 0, 1}, .position = {1, 2, 3}},
	        .linear_velocity = {}, .angular_velocity = {}, .device = device_id::LEFT_GRIP,
	        .flags = from_headset::position_valid | from_headset::orientation_valid |
	                 from_headset::position_tracked | from_headset::orientation_tracked,
	});
	auto sample = [&] {
		packet.production_timestamp = packet.timestamp = os_monotonic_get_ns();
		grip.update_tracking(packet, offset);
		return grip.get_at(packet.timestamp).second;
	};
	auto first = sample();
	assert(first.relation_flags & XRT_SPACE_RELATION_POSITION_TRACKED_BIT);
	assert(first.pose.position.x == 1);
	std::this_thread::sleep_for(std::chrono::milliseconds(3));
	packet.device_poses[0].flags = from_headset::orientation_valid;
	packet.device_poses[0].pose.position = {};
	auto lost = sample();
	assert(lost.pose.position.x == 1 && lost.pose.position.y == 2 && lost.pose.position.z == 3);
	assert(!(lost.relation_flags & XRT_SPACE_RELATION_POSITION_TRACKED_BIT));
	std::this_thread::sleep_for(std::chrono::milliseconds(3));
	packet.device_poses[0].flags = from_headset::position_valid | from_headset::orientation_valid;
	assert(sample().pose.position.x == 1); // valid estimated/idle neutral data cannot move it
	std::this_thread::sleep_for(std::chrono::milliseconds(3));
	packet.device_poses[0].flags |= from_headset::position_tracked | from_headset::orientation_tracked;
	packet.device_poses[0].pose.position = {7, 2, 3};
	auto resumed = sample();
	assert(resumed.relation_flags & XRT_SPACE_RELATION_POSITION_TRACKED_BIT);
	assert(std::abs(resumed.pose.position.x - 7) < 0.01f);
	std::this_thread::sleep_for(std::chrono::milliseconds(160));
	auto stale = grip.get_at(os_monotonic_get_ns()).second;
	assert(stale.pose.position.x == resumed.pose.position.x);
	assert(stale.relation_flags == (XRT_SPACE_RELATION_POSITION_VALID_BIT | XRT_SPACE_RELATION_ORIENTATION_VALID_BIT));
	assert(stale.linear_velocity.x == 0 && stale.angular_velocity.x == 0);
	grip.reset(true);
	auto idle = grip.get_at(os_monotonic_get_ns()).second;
	assert(idle.pose.position.x == resumed.pose.position.x);
	assert(idle.relation_flags == stale.relation_flags);
	grip.reset();
	assert(grip.get_at(os_monotonic_get_ns()).second.relation_flags == 0);
}
