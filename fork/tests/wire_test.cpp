// SPDX-License-Identifier: GPL-3.0-or-later
#include "protocol_version.h"
#include "spatial_protocol.h"
#include "wivrn_serialization.h"
#include <cassert>
#include <iostream>
#include <unistd.h>
int main()
{
	wivrn::spatial::snapshot source;
	source.stream = 42;
	source.sequence = 17;
	source.generation = 3;
	source.entity_count = 64;
	source.markers[11].pose.pose.position = {1, 2, 3};
	source.entities[63].boundary_count = 32;
	source.entities[63].boundary[31] = {.5f, .75f};
	source.entities[63].labels[127] = 'Z';
	wivrn::serialization_packet packet;
	packet.serialize(source);
	auto & spans = static_cast<std::vector<std::span<uint8_t>> &>(packet);
	assert(spans.size() + 2 < size_t(sysconf(_SC_IOV_MAX)));
	std::vector<uint8_t> wire;
	for (auto span: spans)
		wire.insert(wire.end(), span.begin(), span.end());
	assert(wire.size() < 65535);
	wivrn::deserialization_packet reader({}, wire);
	auto result = reader.deserialize<wivrn::spatial::snapshot>();
	assert(reader.empty());
	assert(result.stream == 42 && result.sequence == 17 && result.generation == 3);
	assert(result.entity_count == 64 && result.entities[63].boundary_count == 32);
	assert(result.markers[11].pose.pose.position.z == 3 && result.entities[63].boundary[31].y == .75f);
	assert(result.entities[63].labels[127] == 'Z');
	bool rejected = false;
	try
	{
		wivrn::deserialization_packet truncated({}, std::span(wire).first(wire.size() - 1));
		(void)truncated.deserialize<wivrn::spatial::snapshot>();
	}
	catch (const wivrn::deserialization_error &)
	{
		rejected = true;
	}
	assert(rejected);
	wivrn::to_headset::video_stream_description description{};
	description.width = 2048;
	description.height = 2048;
	description.frame_rate = 72;
	description.refresh_rate = 72;
	description.first_frame = (uint64_t(1) << 40) + 257;
	wivrn::serialization_packet video_packet;
	video_packet.serialize(description);
	auto & video_spans = static_cast<std::vector<std::span<uint8_t>> &>(video_packet);
	std::vector<uint8_t> video_wire;
	for (auto span: video_spans)
		video_wire.insert(video_wire.end(), span.begin(), span.end());
	wivrn::deserialization_packet video_reader({}, video_wire);
	assert(video_reader.deserialize<wivrn::to_headset::video_stream_description>() == description);
	assert(video_reader.empty());
	struct legacy_video_description
	{
		uint16_t width, height;
		std::array<wivrn::video_codec, 3> codec;
		float frame_rate, refresh_rate;
	};
	static_assert(wivrn::serialization_type_hash<legacy_video_description>(0) !=
	              wivrn::serialization_type_hash<wivrn::to_headset::video_stream_description>(0));
	std::cout << "Protocol hash: " << std::hex << wivrn::protocol_version << std::dec << "\n";
	std::cout << "Native wire round trip: " << wire.size() << " bytes, " << spans.size() << " spans; truncation rejected\n";
}
