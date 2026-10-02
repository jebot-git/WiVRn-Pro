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
	assert(s.frames == 1 && s.repeats == 0);
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
