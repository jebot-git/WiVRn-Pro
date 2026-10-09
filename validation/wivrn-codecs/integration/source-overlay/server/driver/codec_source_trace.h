// SPDX-License-Identifier: MIT
#pragma once

// Test-only source-generation evidence. No clock value here is a photon time.
#ifdef WIVRN_CODEC_TESTS
#include <algorithm>
#include <atomic>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <stdexcept>
#include <thread>

namespace wivrn::codec_test
{
enum class source_event : unsigned
{
	app_predict, app_point, app_delivered, app_gpu_done, app_discarded,
	app_latched, app_retired, compositor_frame, projection_layer,
};

struct source_record
{
	source_event event{};
	uint64_t pacer_id = 0;
	int64_t app_frame_id = -1, system_frame_id = -1;
	int64_t when_ns = 0, observed_ns = 0;
	int64_t display_time_ns = 0, display_period_ns = 0, wake_up_ns = 0;
	int64_t layer_target_ns = 0;
	uint32_t layer_count = 0, projection_count = 0, layer_index = 0;
	uint32_t timing_point = 0;
	bool direct_projection = false, projection_pose_reused = false;
};

// Append only: two writer-accounting atomics and one reservation per accepted
// event, no allocation, locks, formatting, syscalls, or blocking in append().
// flush() is teardown-only. It closes admission, waits for existing appenders,
// then serializes immutable records. A full buffer invalidates the capture.
template <size_t Capacity = 131072>
class source_trace_buffer
{
	std::unique_ptr<source_record[]> records;
	std::atomic<bool> active{false};
	std::atomic<size_t> writers{0}, next{0}, dropped{0};
	FILE * output = nullptr;

public:
	explicit source_trace_buffer(const char * path)
	{
		if (!path || !*path)
			return;
		if (path[0] != '/')
			throw std::runtime_error("WIVRN_CODEC_SOURCE_TRACE must be an absolute new file path");
		records = std::make_unique<source_record[]>(Capacity);
		output = std::fopen(path, "wx");
		if (!output)
			throw std::runtime_error("cannot create WIVRN_CODEC_SOURCE_TRACE output");
		active = true;
	}

	~source_trace_buffer() { flush(); }
	source_trace_buffer(const source_trace_buffer &) = delete;
	source_trace_buffer & operator=(const source_trace_buffer &) = delete;

	bool enabled() const noexcept { return active.load(std::memory_order_relaxed); }

	void append(source_record value) noexcept
	{
		// Sequential consistency makes close/admission ordering explicit. A
		// writer arriving after close never touches the record array.
		writers.fetch_add(1);
		if (active.load())
		{
			const auto index = next.fetch_add(1);
			if (index < Capacity)
				records[index] = value;
			else
				dropped.fetch_add(1);
		}
		writers.fetch_sub(1);
	}

	void flush() noexcept
	{
		if (!active.exchange(false))
			return;
		while (writers.load())
			std::this_thread::yield();
		const size_t count = std::min(next.load(), Capacity);
		std::fprintf(output, "{\"type\":\"header\",\"schema\":1,\"clock\":\"host_monotonic_ns\",\"capacity\":%zu,\"diagnostic_only\":true}\n", Capacity);
		constexpr const char * names[] = {"app_predict", "app_point", "app_delivered", "app_gpu_done", "app_discarded", "app_latched", "app_retired", "compositor_frame", "projection_layer"};
		for (size_t i = 0; i < count; ++i)
		{
			const auto & r = records[i];
			std::fprintf(output,
			             "{\"type\":\"event\",\"sequence\":%zu,\"event\":\"%s\",\"pacer_id\":%llu,\"app_frame_id\":%lld,\"system_frame_id\":%lld,\"when_ns\":%lld,\"observed_ns\":%lld,\"display_time_ns\":%lld,\"display_period_ns\":%lld,\"wake_up_ns\":%lld,\"layer_target_ns\":%lld,\"layer_count\":%u,\"projection_count\":%u,\"layer_index\":%u,\"timing_point\":%u,\"direct_projection\":%s,\"projection_pose_reused\":%s}\n",
			             i + 1, names[unsigned(r.event)], (unsigned long long)r.pacer_id,
			             (long long)r.app_frame_id, (long long)r.system_frame_id,
			             (long long)r.when_ns, (long long)r.observed_ns,
			             (long long)r.display_time_ns, (long long)r.display_period_ns,
			             (long long)r.wake_up_ns, (long long)r.layer_target_ns,
			             r.layer_count, r.projection_count, r.layer_index, r.timing_point,
			             r.direct_projection ? "true" : "false",
			             r.projection_pose_reused ? "true" : "false");
		}
		// A partial/failed write cannot produce a valid complete capture.
		if (!std::ferror(output))
			std::fprintf(output, "{\"type\":\"footer\",\"records\":%zu,\"dropped\":%zu,\"complete\":true}\n", count, dropped.load());
		std::fclose(output);
		output = nullptr;
	}
};

inline auto & source_trace()
{
	// Initialize from pacing factory/compositor construction, before callbacks.
	static source_trace_buffer<> value(std::getenv("WIVRN_CODEC_SOURCE_TRACE"));
	return value;
}
} // namespace wivrn::codec_test
#endif
