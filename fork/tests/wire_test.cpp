// SPDX-License-Identifier: GPL-3.0-or-later
#include "protocol_version.h"
#include "spatial_protocol.h"
#include "wivrn_serialization.h"
#include "packed444/layout.h"
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
	// Exercise the complete capture variants, including a maximum-size JPEG.
	auto round_trip = []<typename T>(const T & value) {
		wivrn::serialization_packet serialized;
		serialized.serialize(value);
		std::vector<uint8_t> bytes;
		for (auto span : static_cast<std::vector<std::span<uint8_t>> &>(serialized))
			bytes.insert(bytes.end(), span.begin(), span.end());
		wivrn::deserialization_packet input({}, bytes);
		auto output = input.deserialize<T>();
		assert(input.empty());
		return output;
	};
	wivrn::to_headset::packets control = wivrn::to_headset::headset_capture_control{.request_id = (uint64_t(1) << 48) + 37, .enabled = true, .frames = false};
	auto control_copy = std::get<wivrn::to_headset::headset_capture_control>(round_trip(control));
	assert(control_copy.request_id == (uint64_t(1) << 48) + 37 && control_copy.enabled && !control_copy.frames);
	wivrn::from_headset::packets capture = wivrn::from_headset::headset_capture{.request_id = control_copy.request_id, .age_ms = 123, .status = "ready", .jpeg = std::vector<std::byte>(256 * 1024, std::byte{0xd8})};
	auto capture_copy = std::get<wivrn::from_headset::headset_capture>(round_trip(capture));
	assert(capture_copy.request_id == control_copy.request_id && capture_copy.age_ms == 123 && capture_copy.status == "ready");
	assert(capture_copy.jpeg == std::get<wivrn::from_headset::headset_capture>(capture).jpeg);
	std::cout << "Protocol hash: " << std::hex << wivrn::protocol_version << std::dec << "\n";
	assert(description.layout == wivrn::video_frame_layout::standard);
	description.layout = wivrn::video_frame_layout::packed444_v1;
	assert(round_trip(description) == description);
	// A legacy peer must fail protocol matching instead of displaying carrier planes.
	struct pre_packed_description
	{
		uint16_t width, height;
		std::array<wivrn::video_codec, 3> codec;
		float frame_rate, refresh_rate;
		uint8_t pyrowave_chroma, pyrowave_wavelet;
		uint64_t first_frame;
	};
	static_assert(wivrn::serialization_type_hash<pre_packed_description>(0) !=
	              wivrn::serialization_type_hash<wivrn::to_headset::video_stream_description>(0));
	wivrn::from_headset::headset_info_packet info{};
	assert(!round_trip(info).supports_packed444);
	info.supports_packed444 = true;
	info.supported_codecs = {{.codec = wivrn::h265, .supports_10bit = true}};
	assert(round_trip(info).supports_packed444);
	assert(round_trip(info).supported_codecs == info.supported_codecs);
	using wivrn::packed444::coded_extent;
	using wivrn::packed444::logical_extent;
	for (auto logical: {wivrn::packed444::extent{2, 2}, {1472, 1600}, {32766, 65534}})
	{
		auto coded = coded_extent(logical.width, logical.height);
		assert(logical_extent(coded.width, coded.height) == logical);
	}
	for (auto invalid: {std::array<uint32_t, 2>{0, 2}, {2, 0}, {3, 2}, {2, 3}, {32768, 2}, {2, 65536}})
	{
		bool invalid_rejected = false;
		try { (void)coded_extent(invalid[0], invalid[1]); }
		catch (const std::invalid_argument &) { invalid_rejected = true; }
		assert(invalid_rejected);
	}
	std::cout << "Native wire round trip: " << wire.size() << " bytes, " << spans.size() << " spans; truncation rejected\n";
}
