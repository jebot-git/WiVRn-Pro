// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "utils/ring_buffer.h"
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <functional>
#include <span>
#include <thread>

namespace wivrn
{
// Single AAudio callback producer; a single worker owns DSP and network sends.
// enqueue() never allocates, waits, logs, or sends packets. Call set_enabled(false)
// before stopping capture; old queued/partial frames are invalidated by an epoch.
class software_microphone
{
public:

	using sender = std::function<void(std::span<int16_t>, int64_t)>;
	software_microphone(int sample_rate, bool denoise, sender send);
	~software_microphone();
	software_microphone(const software_microphone &) = delete;
	software_microphone & operator=(const software_microphone &) = delete;

	void set_enabled(bool enabled);
	void enqueue(std::span<const int16_t> samples, int64_t timestamp);
	bool failed() const { return send_failed.load(); }
	uint64_t dropped() const { return dropped_chunks.load(); }

private:

	static constexpr size_t max_frame_size = 1920; // 10 ms at up to 192 kHz, mono
	struct chunk
	{
		std::array<int16_t, max_frame_size> samples;
		size_t count;
		int64_t timestamp;
		uint64_t epoch, sequence;
		std::chrono::steady_clock::time_point queued;
	};

	const int sample_rate;
	const size_t frame_size;
	const bool denoise;
	sender send;
	utils::ring_buffer<chunk, 8> pending;
	// Even epochs disable forwarding, odd epochs enable a fresh capture interval.
	std::atomic<uint64_t> epoch = 0;
	std::atomic<uint64_t> wake = 0;
	std::atomic<bool> stopping = false;
	std::atomic<bool> send_failed = false;
	std::atomic<uint64_t> dropped_chunks = 0;
	uint64_t sequence = 0; // producer only
	std::thread worker;

	void run();
	void notify();
};
} // namespace wivrn
