/*
 * WiVRn VR streaming
 * Copyright (C) 2022  Guillaume Meunier <guillaume.meunier@centraliens.net>
 * Copyright (C) 2022  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#pragma once

#include "utils/ring_buffer.h"
#include "wivrn_packets.h"
#include <atomic>
#include <condition_variable>
#include <mutex>
#include <thread>

struct AAudioStreamStruct;
struct AAudioStreamBuilderStruct;

class wivrn_session;

namespace xr
{
class instance;
}

namespace wivrn::android
{
class audio
{
	static int32_t speaker_data_cb(AAudioStreamStruct *, void *, void *, int32_t);
	static int32_t microphone_data_cb(AAudioStreamStruct *, void *, void *, int32_t);

	static void microphone_error_cb(AAudioStreamStruct *, void *, int32_t);
	static void speaker_error_cb(AAudioStreamStruct *, void *, int32_t);

	void audio_worker();
	void build_microphone(AAudioStreamBuilderStruct *, int32_t, int32_t);
	void build_speaker(AAudioStreamBuilderStruct *, int32_t, int32_t);
	struct microphone_block
	{
		std::array<uint8_t, 16384> data{};
		size_t size = 0;
		int64_t timestamp = 0;
		uint64_t generation = 0;
	};
	utils::ring_buffer<microphone_block, 32> microphone_samples;
	std::atomic<uint64_t> microphone_generation = 0;
	std::atomic<uint64_t> microphone_frames = 0;
	std::atomic<uint64_t> microphone_drops = 0;
	std::atomic<bool> mic_requested = false;
	std::atomic<bool> mic_restart = false;
	std::atomic<int32_t> mic_error = 0, speaker_error = 0;
	const wivrn::to_headset::audio_stream_description description;
	utils::ring_buffer<wivrn::audio_data, 100> output_buffer;
	std::atomic<size_t> buffer_size_bytes = 0;

	wivrn::audio_data speaker_tmp;
	AAudioStreamStruct * speaker = nullptr;
	AAudioStreamStruct * microphone = nullptr;

	wivrn_session & session;
	xr::instance & instance;

	std::mutex mutex;
	std::atomic<bool> exiting = false;
	std::condition_variable wake;
	std::thread worker;

public:
	audio(const audio &) = delete;
	audio & operator=(const audio &) = delete;
	audio(const wivrn::to_headset::audio_stream_description &, wivrn_session &, xr::instance &);
	~audio();

	void operator()(wivrn::audio_data &&);

	void set_mic_state(bool running);
	void restart_microphone();
	// Called only by the network thread; never send TCP from an AAudio callback.
	void send_microphone_data();

	static void get_audio_description(wivrn::from_headset::headset_info_packet & info);
};
} // namespace wivrn::android
