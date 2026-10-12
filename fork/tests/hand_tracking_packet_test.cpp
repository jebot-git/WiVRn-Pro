// SPDX-License-Identifier: GPL-3.0-or-later
#include "hand_tracking_packet.h"
#include "wivrn_serialization.h"
#include <cassert>
#include <cmath>

using packet_type = wivrn::from_headset::hand_tracking;

static packet_type round_trip(const packet_type & source)
{
	wivrn::serialization_packet packet;
	packet.serialize(source);
	std::vector<uint8_t> wire;
	for (auto bytes: static_cast<std::vector<std::span<uint8_t>> &>(packet))
		wire.insert(wire.end(), bytes.begin(), bytes.end());
	wivrn::deserialization_packet reader({}, wire);
	auto result = reader.deserialize<packet_type>();
	assert(reader.empty());
	return result;
}

int main()
{
	xr::hand_tracker::located_hand hand{};
	for (size_t i = 0; i < hand.joints.size(); ++i)
	{
		auto & [location, velocity] = hand.joints[i];
		location.pose = {{0, 0, 0, 1}, {float(i), .25f, -.5f}};
		location.radius = .0125f;
		location.locationFlags = XR_SPACE_LOCATION_POSITION_VALID_BIT | XR_SPACE_LOCATION_ORIENTATION_VALID_BIT |
		                         XR_SPACE_LOCATION_POSITION_TRACKED_BIT | XR_SPACE_LOCATION_ORIENTATION_TRACKED_BIT;
		velocity.linearVelocity = {1, 2, 3};
		velocity.angularVelocity = {4, 5, 6};
		velocity.velocityFlags = XR_SPACE_VELOCITY_LINEAR_VALID_BIT | XR_SPACE_VELOCITY_ANGULAR_VALID_BIT;
	}

	// A controller skeleton is excluded from local gestures but must reach the
	// server intact, explicitly marked so it cannot become bare-hand input there.
	hand.data_source = XR_HAND_TRACKING_DATA_SOURCE_CONTROLLER_EXT;
	assert(not hand.is_input_source());
	auto controller = round_trip(wivrn::make_hand_tracking_packet(hand, 100, 120, packet_type::right));
	assert(controller.source == packet_type::data_source::controller);
	assert(controller.production_timestamp == 100 && controller.timestamp == 120);
	assert(controller.hand == packet_type::right && controller.joints);
	for (size_t i = 0; i < controller.joints->size(); ++i)
	{
		const auto & joint = (*controller.joints)[i];
		assert(joint.position.x == float(i) && joint.position.y == .25f && joint.position.z == -.5f);
		assert(joint.linear_velocity.x == 1 && joint.linear_velocity.y == 2 && joint.linear_velocity.z == 3);
		assert(joint.angular_velocity.x == 4 && joint.angular_velocity.y == 5 && joint.angular_velocity.z == 6);
		assert(joint.radius == 125 && joint.flags == 0x3f);
		const XrQuaternionf orientation = joint.orientation;
		assert(std::abs(orientation.w - 1) < .001f);
	}

	hand.data_source = XR_HAND_TRACKING_DATA_SOURCE_UNOBSTRUCTED_EXT;
	assert(hand.is_input_source());
	auto unobstructed = round_trip(wivrn::make_hand_tracking_packet(hand, 200, 220, packet_type::left));
	assert(unobstructed.joints && unobstructed.hand == packet_type::left);
	assert(unobstructed.source == packet_type::data_source::unobstructed);

	// Quest and other runtimes without the source extension retain their previous
	// behavior; inactive tracking never fabricates joints.
	hand.data_source.reset();
	assert(hand.is_input_source());
	auto legacy = round_trip(wivrn::make_hand_tracking_packet(hand, 300, 320, packet_type::left));
	assert(legacy.joints && legacy.source == packet_type::data_source::unobstructed);
	auto inactive = round_trip(wivrn::make_hand_tracking_packet(std::nullopt, 400, 420, packet_type::right));
	assert(not inactive.joints);
	assert(inactive.production_timestamp == 400 && inactive.timestamp == 420 && inactive.hand == packet_type::right);
	assert(packet_type{}.source == packet_type::data_source::unobstructed);

	struct legacy_packet
	{
		XrTime production_timestamp;
		XrTime timestamp;
		packet_type::hand_id hand;
		std::optional<std::array<packet_type::pose, XR_HAND_JOINT_COUNT_EXT>> joints;
	};
	static_assert(wivrn::serialization_type_hash<legacy_packet>(0) != wivrn::serialization_type_hash<packet_type>(0));
}
