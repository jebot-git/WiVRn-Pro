// SPDX-License-Identifier: GPL-3.0-or-later
#include "hand_joints_list.h"

#include <array>
#include <cassert>
#include <cmath>
#include <iostream>

using namespace wivrn;
using packet = from_headset::hand_tracking;
using source = packet::data_source;

namespace
{
const clock_offset offset{.b = 7'000'000, .stable = true};
constexpr XrTime begin = 2'000'000'000;
constexpr XrTime ms = 1'000'000;

packet sample(packet::hand_id hand, source data_source, XrTime server_time, float wrist_x)
{
	packet result{
	        .production_timestamp = server_time - 5 * ms + offset.b,
	        .timestamp = server_time + offset.b,
	        .hand = hand,
	        .joints = std::array<packet::pose, XR_HAND_JOINT_COUNT_EXT>{},
	        .source = data_source,
	};
	for (int i = 0; i < XR_HAND_JOINT_COUNT_EXT; ++i)
	{
		(*result.joints)[i] = {
		        .position = {wrist_x + 0.01f * (i - XR_HAND_JOINT_WRIST_EXT), 1.0f, -0.5f},
		        .orientation = pack(XrQuaternionf{0, 0, 0, 1}),
		        .radius = 100,
		        .flags = from_headset::position_valid | from_headset::orientation_valid |
		                 from_headset::position_tracked | from_headset::orientation_tracked,
		};
	}
	return result;
}

void near(float actual, float expected)
{
	assert(std::abs(actual - expected) < 0.0001f);
}

void assert_active(hand_joints_list & history, XrTime at, float wrist_x)
{
	auto [produced, hand] = history.get_at(at);
	assert(produced > 0);
	assert(hand.is_active);
	near(hand.hand_pose.pose.position.x, wrist_x);
	near(hand.hand_pose.pose.position.y, 1.0f);
	assert(hand.hand_pose.relation_flags & XRT_SPACE_RELATION_POSITION_VALID_BIT);
	const auto & tip = hand.values.hand_joint_set_default[XRT_HAND_JOINT_INDEX_TIP];
	near(tip.relation.pose.position.x, 0.01f * (XR_HAND_JOINT_INDEX_TIP_EXT - XR_HAND_JOINT_WRIST_EXT));
	near(tip.radius, 0.01f);
}

void assert_inactive(hand_joints_list & history, XrTime at)
{
	const auto hand = history.get_at(at).second;
	assert(!hand.is_active);
	assert(hand.hand_pose.relation_flags == 0);
}
} // namespace

int main()
{
	hand_joints_list left_optical(packet::left);
	hand_joints_list left_controller(packet::left, source::controller);
	hand_joints_list right_optical(packet::right);
	hand_joints_list right_controller(packet::right, source::controller);
	const std::array histories{&left_optical, &left_controller, &right_optical, &right_controller};
	auto receive = [&](const packet & value) {
		for (auto * history: histories)
			history->update_tracking(value, offset);
	};

	// Controller-derived joints must never activate the unobstructed source.
	const auto first = sample(packet::left, source::controller, begin, 10.0f);
	receive(first);
	assert_active(left_controller, begin, 10.0f);
	assert(left_controller.get_at(begin).first == begin - 5 * ms);
	assert_inactive(left_optical, begin);
	assert_inactive(right_optical, begin);
	assert_inactive(right_controller, begin);

	// The other hand may use a different source without replacing this hand.
	receive(sample(packet::right, source::unobstructed, begin + 10 * ms, 20.0f));
	assert_active(right_optical, begin + 10 * ms, 20.0f);
	assert_inactive(right_controller, begin + 10 * ms);
	assert_active(left_controller, begin + 10 * ms, 10.0f);
	assert(left_controller.get_at(begin + 10 * ms).first == begin - 5 * ms);

	// Switching to optical tracking deactivates the controller channel, while
	// past queries retain the source that actually produced their sample.
	receive(sample(packet::left, source::unobstructed, begin + 20 * ms, 2.0f));
	assert_active(left_optical, begin + 20 * ms, 2.0f);
	assert_inactive(left_controller, begin + 20 * ms);
	assert_active(left_controller, begin, 10.0f);
	assert_inactive(left_optical, begin);
	assert_inactive(left_controller, begin + 10 * ms);
	assert_inactive(left_optical, begin + 10 * ms);

	// Ordinary interpolation within one active source remains available.
	receive(sample(packet::left, source::unobstructed, begin + 40 * ms, 4.0f));
	assert_active(left_optical, begin + 30 * ms, 3.0f);
	assert_inactive(left_controller, begin + 30 * ms);

	// Unknown wire values are not silently treated as optical tracking.
	receive(sample(packet::left, static_cast<source>(255), begin + 50 * ms, 99.0f));
	assert_inactive(left_optical, begin + 50 * ms);
	assert_inactive(left_controller, begin + 50 * ms);

	// Inactive tracking clears both channels, and an older packet must not
	// resurrect the last pose after that clear.
	const auto resumed = sample(packet::left, source::controller, begin + 60 * ms, 6.0f);
	receive(resumed);
	assert_active(left_controller, begin + 60 * ms, 6.0f);
	assert_inactive(left_optical, begin + 60 * ms);
	auto lost = sample(packet::left, source::controller, begin + 80 * ms, 0.0f);
	lost.joints.reset();
	receive(lost);
	assert_inactive(left_controller, begin + 80 * ms);
	assert_inactive(left_optical, begin + 80 * ms);
	receive(resumed);
	assert_inactive(left_controller, begin + 80 * ms);
	assert_inactive(left_optical, begin + 80 * ms);
	assert_active(right_optical, begin + 80 * ms, 20.0f);

	// Legacy/default source values preserve optical hand tracking.
	auto optical = sample(packet::left, source::unobstructed, begin + 100 * ms, 1.0f);
	packet default_source{
	        .production_timestamp = optical.production_timestamp,
	        .timestamp = optical.timestamp,
	        .hand = optical.hand,
	        .joints = optical.joints,
	};
	receive(default_source);
	assert_active(left_optical, begin + 100 * ms, 1.0f);
	assert_inactive(left_controller, begin + 100 * ms);

	// The underlying history still expires; the controller's stricter 150 ms
	// wall-clock policy is applied separately in get_hand_tracking().
	assert_inactive(left_optical, begin + 100 * ms + 1'000'000'001);

	std::cout << "Hand joint histories: source isolation, transitions, ordering and expiry passed\n";
}
