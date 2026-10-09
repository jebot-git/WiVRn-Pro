// SPDX-License-Identifier: MIT
// Build with -DWIVRN_CODEC_TESTS=1 and -I <test source>/server/driver.
#include "codec_source_trace.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <string>
#include <vector>
#include <unistd.h>

using wivrn::codec_test::source_event;
using wivrn::codec_test::source_record;
using wivrn::codec_test::source_trace_buffer;

static std::string read(const std::string & path)
{
	std::ifstream file(path);
	return {std::istreambuf_iterator<char>(file), {}};
}

int main()
{
	const auto prefix = std::filesystem::temp_directory_path().string() + "/source-trace-test-" + std::to_string(getpid());
	{
		source_trace_buffer<4> disabled(nullptr);
		assert(!disabled.enabled());
		disabled.append({});
		disabled.flush();
	}
	bool threw = false;
	try { source_trace_buffer<4> invalid("relative.jsonl"); }
	catch (const std::runtime_error &) { threw = true; }
	assert(threw);
	{
		const auto path = prefix + "-overflow.jsonl";
		source_trace_buffer<4> trace(path.c_str());
		for (int i = 0; i < 7; ++i)
			trace.append({.event = source_event::app_predict, .app_frame_id = i});
		assert(read(path).empty()); // no header/event writes on the hot path
		trace.flush();
		const auto text = read(path);
		assert(text.find("\"records\":4,\"dropped\":3,\"complete\":true") != std::string::npos);
		trace.append({});
		trace.flush();
		assert(read(path) == text);
		threw = false;
		try { source_trace_buffer<4> existing(path.c_str()); }
		catch (const std::runtime_error &) { threw = true; }
		assert(threw);
		std::filesystem::remove(path);
	}
	{
		const auto path = prefix + "-threads.jsonl";
		source_trace_buffer<8192> trace(path.c_str());
		std::vector<std::thread> workers;
		for (int t = 0; t < 8; ++t)
			workers.emplace_back([&, t] {
				for (int i = 0; i < 1000; ++i)
					trace.append({.event = source_event::app_point, .pacer_id = uint64_t(t + 1),
					              .app_frame_id = i + 1, .when_ns = 123, .observed_ns = 456});
			});
		for (auto & worker : workers) worker.join();
		trace.flush();
		const auto text = read(path);
		assert(text.find("\"records\":8000,\"dropped\":0,\"complete\":true") != std::string::npos);
		assert(std::count(text.begin(), text.end(), '\n') == 8002);
		for (int i = 1; i <= 8000; ++i)
			assert(text.find("\"sequence\":" + std::to_string(i) + ",") != std::string::npos);
		std::filesystem::remove(path);
	}
	{
		const auto path = prefix + "-close.jsonl";
		source_trace_buffer<131072> trace(path.c_str());
		std::atomic<size_t> entered{0};
		std::vector<std::thread> workers;
		for (int t = 0; t < 8; ++t)
			workers.emplace_back([&] {
				entered.fetch_add(1);
				while (trace.enabled()) trace.append({.when_ns = 123, .observed_ns = 456});
			});
		while (entered.load() != 8) std::this_thread::yield();
		trace.flush(); // race teardown against ongoing appenders
		for (auto & worker : workers) worker.join();
		const auto text = read(path);
		const auto lines = std::count(text.begin(), text.end(), '\n');
		assert(text.find("\"records\":" + std::to_string(lines - 2) + ",") != std::string::npos);
		assert(text.find("\"when_ns\":0") == std::string::npos); // no partially written slot
		std::filesystem::remove(path);
	}
}
