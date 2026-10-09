// SPDX-License-Identifier: MIT
#include "codec_pacer_trace.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <random>
#include <string>
#include <vector>
#include <unistd.h>
using namespace wivrn::codec_test;

static std::string read(const std::string & path)
{
	std::ifstream stream(path);
	return {std::istreambuf_iterator<char>(stream), {}};
}

// Test data generator only: Python independently checks every serialized input
// and output. Native predictor execution is separately syntax checked via VFS.
static pacer_record fixture(int64_t frame, int64_t previous, std::mt19937 & random)
{
	pacer_record r{
	        .pacer_id = 1, .frame_id = frame,
	        .now_ns = 1'000'000'000 + frame * 11'111'111,
	        .previous_client_render_ns = previous,
	        .period_ns = 11'111'111,
	        .wake_to_present_ns = 1'000'000 + int64_t(random() % 3'000'000),
	        .safe_decode_ns = int64_t(random() % 30'000'000),
	        .render_to_display_ns = int64_t(random() % 12'000'000),
	        .margin_ns = 3'000'000, .slop_ns = 500'000};
	r.client_render_ns = previous + r.period_ns;
	const auto needed = r.now_ns + r.wake_to_present_ns + r.safe_decode_ns;
	if (needed > r.client_render_ns)
		r.client_render_ns += r.period_ns * ((needed - r.client_render_ns) / r.period_ns);
	r.desired_present_ns = r.client_render_ns - r.safe_decode_ns;
	r.wake_up_ns = r.desired_present_ns - r.wake_to_present_ns + r.margin_ns;
	r.display_ns = r.client_render_ns + r.render_to_display_ns;
	return r;
}

int main(int argc, char ** argv)
{
	if (argc == 3 && std::string(argv[1]) == "--fixtures")
	{
		pacer_trace_buffer<512> trace(argv[2]);
		std::mt19937 random(20261009);
		int64_t previous = 0;
		for (int i = 0; i < 512; ++i)
		{
			const auto r = fixture(i, previous, random);
			trace.append(r);
			// Feedback can adjust phase between predictions. The recorder does
			// not claim this adjustment was measured or explain its origin.
			previous = r.client_render_ns + int64_t(random() % 2'000'001) - 1'000'000;
		}
		return 0;
	}
	const auto prefix = std::filesystem::temp_directory_path().string() + "/pacer-trace-test-" + std::to_string(getpid());
	{
		pacer_trace_buffer<4> disabled(nullptr);
		assert(!disabled.enabled()); disabled.append({}); disabled.flush();
	}
	bool threw = false;
	try { pacer_trace_buffer<4> invalid("relative"); }
	catch (const std::runtime_error &) { threw = true; }
	assert(threw);
	{
		const auto path = prefix + "-full.jsonl";
		pacer_trace_buffer<4> trace(path.c_str());
		for (int i = 0; i < 7; ++i) trace.append({.pacer_id = 1, .frame_id = i});
		assert(read(path).empty());
		trace.flush();
		const auto saved = read(path);
		assert(saved.find("\"records\":4,\"dropped\":3,\"complete\":true") != std::string::npos);
		trace.append({}); trace.flush();
		assert(read(path) == saved);
		threw = false;
		try { pacer_trace_buffer<4> existing(path.c_str()); }
		catch (const std::runtime_error &) { threw = true; }
		assert(threw);
		std::filesystem::remove(path);
	}
	{
		const auto path = prefix + "-parallel.jsonl";
		pacer_trace_buffer<8192> trace(path.c_str());
		std::vector<std::thread> workers;
		for (int t = 0; t < 8; ++t) workers.emplace_back([&, t] {
			for (int i = 0; i < 1000; ++i) trace.append({.pacer_id = uint64_t(t + 1), .frame_id = i, .now_ns = 123});
		});
		for (auto & worker : workers) worker.join();
		trace.flush();
		const auto saved = read(path);
		assert(saved.find("\"records\":8000,\"dropped\":0,\"complete\":true") != std::string::npos);
		assert(std::count(saved.begin(), saved.end(), '\n') == 8002);
		for (int i = 1; i <= 8000; ++i) assert(saved.find("\"sequence\":" + std::to_string(i) + ",") != std::string::npos);
		std::filesystem::remove(path);
	}
	{
		const auto path = prefix + "-close.jsonl";
		pacer_trace_buffer<> trace(path.c_str());
		std::atomic<unsigned> entered{0};
		std::vector<std::thread> workers;
		for (int t = 0; t < 8; ++t) workers.emplace_back([&] {
			entered.fetch_add(1);
			while (trace.enabled()) trace.append({.pacer_id = 1, .frame_id = 0, .now_ns = 123});
		});
		while (entered.load() < 8) std::this_thread::yield();
		trace.flush();
		for (auto & worker : workers) worker.join();
		const auto saved = read(path);
		const auto count = std::count(saved.begin(), saved.end(), '\n') - 2;
		assert(saved.find("\"records\":" + std::to_string(count) + ",") != std::string::npos);
		assert(saved.find("\"now_ns\":0") == std::string::npos);
		std::filesystem::remove(path);
	}
	std::cout << "pacer trace checks passed; record bytes=" << sizeof(pacer_record) << '\n';
}
