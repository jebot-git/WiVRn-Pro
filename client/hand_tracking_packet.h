// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "wivrn_packets.h"
#include "xr/hand_tracker.h"

namespace wivrn
{
// Controller-derived joints are useful for application skeletons even though
// they must not drive the client's bare-hand pointer and gesture input.
inline from_headset::hand_tracking make_hand_tracking_packet(
        const std::optional<xr::hand_tracker::located_hand> & located,
        XrTime production_timestamp,
        XrTime timestamp,
        from_headset::hand_tracking::hand_id hand)
{
	from_headset::hand_tracking packet{
	        .production_timestamp = production_timestamp,
	        .timestamp = timestamp,
	        .hand = hand,
	        .joints = std::nullopt,
	};
	if (not located)
		return packet;

	if (located->data_source == XR_HAND_TRACKING_DATA_SOURCE_CONTROLLER_EXT)
		packet.source = from_headset::hand_tracking::data_source::controller;

	auto & poses = packet.joints.emplace();
	for (int i = 0; i < XR_HAND_JOINT_COUNT_EXT; i++)
	{
		const auto & joint = located->joints[i];
		poses[i] = {
		        .position = joint.first.pose.position,
		        .orientation = pack(joint.first.pose.orientation),
		        .linear_velocity = joint.second.linearVelocity,
		        .angular_velocity = joint.second.angularVelocity,
		        .radius = uint16_t(joint.first.radius * 10'000),
		        .flags = from_headset::to_pose_flags(joint.first.locationFlags, joint.second.velocityFlags),
		};
	}
	return packet;
}
} // namespace wivrn
