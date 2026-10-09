// SPDX-License-Identifier: MIT
#pragma once
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
// Snapshot of actual predictor inputs and outputs under the existing pacer
// mutex. This records no feedback-clock conversion or optical measurement.
struct pacer_record
{
	uint64_t pacer_id = 0;
	int64_t frame_id = -1, now_ns = 0, previous_client_render_ns = 0;
	int64_t period_ns = 0, wake_to_present_ns = 0, safe_decode_ns = 0;
	int64_t render_to_display_ns = 0, margin_ns = 0, slop_ns = 0;
	int64_t client_render_ns = 0, desired_present_ns = 0, wake_up_ns = 0, display_ns = 0;
};

// Bounded append-only diagnostic buffer. Serialization occurs only at explicit
// shutdown; a missing footer or any dropped record invalidates a capture.
// Callers must stop accessing the object before destruction (flush can race
// append, destruction cannot). No allocation, formatting or I/O in append.
template <size_t Capacity = 65536>
class pacer_trace_buffer
{
	static_assert(Capacity > 0 && Capacity <= 65536);
	std::unique_ptr<pacer_record[]> records;
	std::atomic<bool> active{false};
	std::atomic<size_t> writers{0}, next{0}, dropped{0};
	FILE * output = nullptr;

public:
	explicit pacer_trace_buffer(const char * path)
	{
		if (!path || !*path) return;
		if (*path != '/') throw std::runtime_error("WIVRN_CODEC_PACER_TRACE requires an absolute new file path");
		records = std::make_unique<pacer_record[]>(Capacity);
		output = std::fopen(path, "wx");
		if (!output) throw std::runtime_error("cannot create WIVRN_CODEC_PACER_TRACE output");
		active = true;
	}
	~pacer_trace_buffer() { flush(); }
	pacer_trace_buffer(const pacer_trace_buffer &) = delete;
	pacer_trace_buffer & operator=(const pacer_trace_buffer &) = delete;
	bool enabled() const noexcept { return active.load(std::memory_order_relaxed); }
	void append(pacer_record record) noexcept
	{
		writers.fetch_add(1);
		if (active.load())
		{
			const auto index = next.fetch_add(1);
			if (index < Capacity) records[index] = record;
			else dropped.fetch_add(1);
		}
		writers.fetch_sub(1);
	}
	void flush() noexcept
	{
		if (!active.exchange(false)) return;
		while (writers.load()) std::this_thread::yield();
		const auto count = std::min(next.load(), Capacity);
		std::fprintf(output, "{\"type\":\"header\",\"schema\":1,\"clock\":\"host_monotonic_ns\",\"capacity\":%zu,\"diagnostic_only\":true,\"coverage\":\"predict_calls_until_close\"}\n", Capacity);
		for (size_t i = 0; i < count; ++i)
		{
			const auto & r = records[i];
			std::fprintf(output,
			             "{\"type\":\"prediction\",\"sequence\":%zu,\"pacer_id\":%llu,\"frame_id\":%lld,\"now_ns\":%lld,\"previous_client_render_ns\":%lld,\"period_ns\":%lld,\"wake_to_present_ns\":%lld,\"safe_decode_ns\":%lld,\"render_to_display_ns\":%lld,\"margin_ns\":%lld,\"slop_ns\":%lld,\"client_render_ns\":%lld,\"desired_present_ns\":%lld,\"wake_up_ns\":%lld,\"display_ns\":%lld}\n",
			             i + 1, (unsigned long long)r.pacer_id, (long long)r.frame_id,
			             (long long)r.now_ns, (long long)r.previous_client_render_ns,
			             (long long)r.period_ns, (long long)r.wake_to_present_ns,
			             (long long)r.safe_decode_ns, (long long)r.render_to_display_ns,
			             (long long)r.margin_ns, (long long)r.slop_ns,
			             (long long)r.client_render_ns, (long long)r.desired_present_ns,
			             (long long)r.wake_up_ns, (long long)r.display_ns);
		}
		if (!std::ferror(output))
			std::fprintf(output, "{\"type\":\"footer\",\"records\":%zu,\"dropped\":%zu,\"complete\":true}\n", count, dropped.load());
		std::fclose(output);
		output = nullptr;
	}
};

inline auto & pacer_trace()
{
	static pacer_trace_buffer<> value(std::getenv("WIVRN_CODEC_PACER_TRACE"));
	return value;
}
inline uint64_t next_pacer_trace_id() noexcept
{
	static std::atomic<uint64_t> counter{0};
	return counter.fetch_add(1) + 1;
}
} // namespace wivrn::codec_test
#endif
