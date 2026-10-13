// SPDX-License-Identifier: GPL-3.0-or-later
#include "performance_feed.h"
#include <cassert>
#include <fstream>
#include <nlohmann/json.hpp>
#include <sys/stat.h>
#include <unistd.h>
// Isolate the publisher from Monado. The running server supplies real pacers.
wivrn::pacing_app_factory::pacing_app_factory() :
        u_pacing_app_factory{} {}
std::vector<wivrn::app_performance> wivrn::pacing_app_factory::performance_snapshot()
{
	return {};
}
using namespace wivrn;
int main()
{
	stream_performance s;
	from_headset::feedback f{};
	f.frame_index = 1;
	f.times_displayed = 1;
	s.update(f, 100);
	s.update(f, 101);
	assert(s.frames == 1 && s.presented_frames == 1 && s.repeats == 0);
	f.times_displayed = 3;
	s.update(f, 102);
	s.update(f, 103);
	assert(s.repeats == 2);
	f.times_displayed = 1;
	s.update(f, 104);
	assert(s.repeats == 2);
	f.frame_index = 2;
	s.update(f, 105);
	f.frame_index = 1;
	f.sent_to_decoder = 100;
	f.received_from_decoder = 120;
	f.displayed = 150;
	f.encode_begin = 90;
	s.update(f, 106);
	assert(s.frames == 2 && s.last.frame_index == 2);
	assert(s.timings[2].start == 100 && s.timings[2].end == 120);
	assert(s.timings[3].end == 150); // late display report survives newer receive report
	f.frame_index = 3;
	f.sent_to_decoder = 200;
	f.received_from_decoder = 199;
	s.update(f, 107);
	assert(s.timings[2].frame == 1); // negative duration rejected
	for (uint64_t i = 4; i < 1000; i++)
	{
		f.frame_index = i;
		s.update(f, 108);
	}
	auto count = s.frames;
	f.frame_index = 1;
	f.times_displayed = 100;
	s.update(f, 109);
	assert(s.frames == count && s.seen.size() == 256 && s.repeats == 2);
	// Coherent measurements survive newer partial feedback and repeated display.
	stream_performance coherent;
	from_headset::feedback full{};
	full.frame_index = 42;
	full.times_displayed = 1;
	full.encode_begin = 1'000'000;
	full.encode_end = 2'000'000;
	full.send_begin = 3'000'000;
	full.received_first_packet = 4'000'000;
	full.received_last_packet = full.sent_to_decoder = 5'000'000;
	full.received_from_decoder = 8'000'000;
	full.blitted = 19'000'000;
	full.displayed = 31'000'000;
	coherent.update(full, 200);
	assert(coherent.first_display.frame_index == 42);
	auto repeat = full;
	repeat.times_displayed = 2;
	repeat.displayed += 11'000'000;
	coherent.update(repeat, 201);
	assert(coherent.first_display.displayed == 31'000'000);
	assert(coherent.first_display_updated_ns == 200);
	auto partial = full;
	partial.frame_index = 43;
	partial.blitted = partial.displayed = 0;
	partial.times_displayed = 0;
	coherent.update(partial, 202);
	assert(coherent.first_display.frame_index == 42);
	assert(coherent.timings[0].frame == 43 && coherent.timings[3].frame == 42);
	auto invalid = full;
	invalid.frame_index = 44;
	invalid.blitted = invalid.received_from_decoder - 1;
	coherent.update(invalid, 203);
	assert(coherent.first_display.frame_index == 42);
	auto older = full;
	older.frame_index = 41;
	coherent.update(older, 204);
	assert(coherent.first_display.frame_index == 42);
	// Received-only feedback is not proof of presentation; a late first display
	// counts exactly once even if receive feedback is duplicated afterwards.
	stream_performance received;
	f = {};
	f.frame_index = 1;
	received.update(f, 110);
	received.update(f, 111);
	assert(received.frames == 1 && received.presented_frames == 0);
	f.times_displayed = 1;
	received.update(f, 112);
	received.update(f, 113);
	f.times_displayed = 0;
	received.update(f, 114);
	assert(received.presented_frames == 1 && received.repeats == 0);
	char directory[] = "/tmp/wivrn-perf-native-XXXXXX";
	assert(mkdtemp(directory));
	pacing_app_factory pacers;
	{
		performance_feed feed(pacers, directory);
		f = {};
		f.frame_index = 10;
		f.stream_index = 0;
		f.received_first_packet = 10;
		f.encode_begin = 1'000'000;
		f.encode_end = 3'000'000;
		f.times_displayed = 2;
		feed.on_feedback(f, 90);
		nlohmann::json data;
		for (int n = 0; n < 30; n++)
		{
			std::ifstream input(std::string(directory) + "/performance.json");
			if (input)
			{
				input >> data;
				if (data.at("streams").size() == 1)
					break;
			}
			std::this_thread::sleep_for(std::chrono::milliseconds(20));
		}
		assert(data.at("version") == 1 && data.at("target_hz") == 90);
		assert(data.at("streams")[0].at("encode_ms") == 2.0);
		assert(data.at("streams")[0].at("decode_ms").is_null());
		assert(data.at("streams")[0].at("repeats") == 1);
		assert(data.at("streams")[0].at("stage_frames").at("encode") == 10);
		assert(data.at("streams")[0].at("first_display").is_null());
		feed.on_feedback(full, 90);
		for (int n = 0; n < 30; n++)
		{
			std::ifstream input(std::string(directory) + "/performance.json");
			input >> data;
			if (!data.at("streams")[0].at("first_display").is_null()) break;
			std::this_thread::sleep_for(std::chrono::milliseconds(20));
		}
		const auto & first = data.at("streams")[0].at("first_display");
		assert(first.at("frame_index") == 42);
		double sum = 0;
		for (const auto * name: {"encode_ms", "encode_to_send_ms", "delivery_ms", "decoder_queue_ms", "decode_ms", "decoded_to_blit_ms", "blit_to_predicted_display_ms"})
			sum += first.at(name).get<double>();
		assert(sum == first.at("encode_to_predicted_display_ms").get<double>());
		assert(sum == 30 && first.at("decoded_to_blit_ms") == 11);
		assert(data.at("streams")[0].at("presented_frames") == 2);
		struct stat st{};
		assert(!stat((std::string(directory) + "/performance.json").c_str(), &st));
		assert((st.st_mode & 0777) == 0600);
		// A competing publisher must not remove the active publisher's snapshot.
		{
			performance_feed contender(pacers, directory);
		}
		assert(std::filesystem::exists(std::string(directory) + "/performance.json"));
	}
	assert(!std::filesystem::exists(std::string(directory) + "/performance.json"));
	std::filesystem::remove_all(directory);
}
