// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "app_pacer.h"
#include "wivrn_packets.h"
#include <array>
#include <condition_variable>
#include <deque>
#include <filesystem>
#include <mutex>
#include <thread>

namespace wivrn
{
// Small, allocation-bounded cache on the feedback path. Disk/JSON work is on the
// worker only. Each stream has its own counters: tiled streams are never added.
struct stream_performance
{
	struct seen_frame
	{
		uint64_t id;
		uint8_t displayed;
	};
	std::deque<seen_frame> seen;
	uint64_t frames = 0, repeats = 0;
	int64_t updated_ns = 0;
	from_headset::feedback last{};
	// A coherent first presentation; legacy stage measurements can refer to
	// different frames and can include later presentations of a repeated frame.
	from_headset::feedback first_display{};
	int64_t first_display_updated_ns = 0;
	struct timing
	{
		uint64_t frame = 0;
		int64_t start = 0, end = 0, updated_ns = 0;
	};
	std::array<timing, 4> timings{};
	void update(const from_headset::feedback & feedback, int64_t now);
};
class performance_feed
{
	pacing_app_factory & pacers;
	std::mutex mutex;
	std::condition_variable wake;
	std::array<stream_performance, 8> streams;
	float target_hz = 0;
	bool stopping = false;
	int directory_fd = -1, lock_fd = -1;
	std::thread worker;
	void run();
	void cleanup();

public:
	explicit performance_feed(pacing_app_factory &, std::filesystem::path directory = {});
	~performance_feed();
	void on_feedback(const from_headset::feedback &, float target);
};
} // namespace wivrn
