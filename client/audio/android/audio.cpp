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

#include "audio.h"

#include "application.h"
#include "utils/audio_recovery.h"
#include "utils/named_thread.h"
#include "wivrn_client.h"
#include "xr/instance.h"

#include "spdlog/spdlog.h"
#include <aaudio/AAudio.h>

int32_t wivrn::android::audio::speaker_data_cb(AAudioStream * stream, void * userdata, void * audio_data_v, int32_t num_frames)
{
	auto self = (wivrn::android::audio *)userdata;
	uint8_t * audio_data = (uint8_t *)audio_data_v;

	if (self->exiting)
	{
		return AAUDIO_CALLBACK_RESULT_STOP;
	}

	size_t frame_size = AAudioStream_getChannelCount(stream) * sizeof(uint16_t);

	while (num_frames != 0)
	{
		// remaining bytes in existing buffer
		ptrdiff_t tmp_remain = self->speaker_tmp.payload.size_bytes();
		// limit to requested frames
		tmp_remain = std::min<ptrdiff_t>(tmp_remain, num_frames * frame_size);
		if (tmp_remain)
		{
			memcpy(audio_data, self->speaker_tmp.payload.data(), tmp_remain);
			self->speaker_tmp.payload = self->speaker_tmp.payload.subspan(tmp_remain);
			audio_data += tmp_remain;
			num_frames -= tmp_remain / frame_size;
			self->buffer_size_bytes.fetch_sub(tmp_remain);
		}
		else
		{
			if (auto tmp = self->output_buffer.read())
			{
				self->speaker_tmp = std::move(*tmp);
			}
			else
			{
				// Buffer underrun: add 5ms buffer
				size_t target_buffer_size = frame_size * AAudioStream_getSampleRate(stream) * 0.005;
				self->speaker_tmp.data.c = std::make_shared<uint8_t[]>(target_buffer_size, 0);
				self->speaker_tmp.payload = std::span(self->speaker_tmp.data.c.get(), target_buffer_size);
				self->buffer_size_bytes += target_buffer_size;
				spdlog::debug("Audio sync: underrun, add {} bytes buffer",
				              target_buffer_size);
			}
		}
	}

	// If we have more than 50ms of buffered data, discard some data
	if (self->buffer_size_bytes > frame_size * AAudioStream_getSampleRate(stream) * 0.05)
	{
		// discard excess data until we only have 30ms left
		size_t target_buffer_size = frame_size * AAudioStream_getSampleRate(stream) * 0.03;
		while (self->buffer_size_bytes > target_buffer_size and self->output_buffer.size() > 1)
		{
			auto tmp = self->output_buffer.read();
			if (not tmp)
				break;
			auto prev = self->buffer_size_bytes.fetch_sub(tmp->payload.size_bytes());
			spdlog::info("Audio sync: discard {} bytes (buffer {} target {})",
			             tmp->payload.size_bytes(),
			             prev,
			             target_buffer_size);
		}
	}

	return AAUDIO_CALLBACK_RESULT_CONTINUE;
}

int32_t wivrn::android::audio::microphone_data_cb(AAudioStream * stream, void * userdata, void * audio_data, int32_t num_frames)
{
	auto self = static_cast<audio *>(userdata);
	if (self->exiting)
		return AAUDIO_CALLBACK_RESULT_STOP;
	self->microphone_frames += num_frames;
	if (!self->mic_requested)
		return AAUDIO_CALLBACK_RESULT_CONTINUE;
	microphone_block block;
	block.size = size_t(num_frames) * AAudioStream_getChannelCount(stream) * sizeof(int16_t);
	if (block.size > block.data.size())
	{
		++self->microphone_drops;
		return AAUDIO_CALLBACK_RESULT_CONTINUE;
	}
	block.timestamp = self->instance.now();
	block.generation = self->microphone_generation;
	memcpy(block.data.data(), audio_data, block.size);
	if (!self->microphone_samples.write(std::move(block)))
		++self->microphone_drops;
	return AAUDIO_CALLBACK_RESULT_CONTINUE;
}

void wivrn::android::audio::build_microphone(AAudioStreamBuilder * builder, int32_t sample_rate, int32_t num_channels)
{
	AAudioStreamBuilder_setDirection(builder, AAUDIO_DIRECTION_INPUT);
	AAudioStreamBuilder_setSampleRate(builder, sample_rate);
	AAudioStreamBuilder_setChannelCount(builder, num_channels);
	AAudioStreamBuilder_setPerformanceMode(builder, AAUDIO_PERFORMANCE_MODE_LOW_LATENCY);
	AAudioStreamBuilder_setSharingMode(builder, AAUDIO_SHARING_MODE_EXCLUSIVE);
	AAudioStreamBuilder_setFormat(builder, AAUDIO_FORMAT_PCM_I16);
	if (application::get_config().mic_unprocessed_audio)
		AAudioStreamBuilder_setInputPreset(builder, AAUDIO_INPUT_PRESET_UNPROCESSED);
	else
		AAudioStreamBuilder_setInputPreset(builder, AAUDIO_INPUT_PRESET_VOICE_COMMUNICATION);

	AAudioStreamBuilder_setDataCallback(builder, &microphone_data_cb, this);
	AAudioStreamBuilder_setErrorCallback(builder, &microphone_error_cb, this);

	aaudio_result_t result = AAudioStreamBuilder_openStream(builder, &microphone);
	if (result != AAUDIO_OK)
		spdlog::error("Cannot create input stream: {}", AAudio_convertResultToText(result));
	if (microphone)
		spdlog::info("Microphone opened: rate={} channels={} sharing={} preset={} performance={}",
		             AAudioStream_getSampleRate(microphone),
		             AAudioStream_getChannelCount(microphone),
		             int(AAudioStream_getSharingMode(microphone)),
		             int(AAudioStream_getInputPreset(microphone)),
		             int(AAudioStream_getPerformanceMode(microphone)));
}

void wivrn::android::audio::build_speaker(AAudioStreamBuilder * builder, int32_t sample_rate, int32_t num_channels)
{
	AAudioStreamBuilder_setDirection(builder, AAUDIO_DIRECTION_OUTPUT);
	AAudioStreamBuilder_setSampleRate(builder, sample_rate);
	AAudioStreamBuilder_setChannelCount(builder, num_channels);
	AAudioStreamBuilder_setPerformanceMode(builder, AAUDIO_PERFORMANCE_MODE_LOW_LATENCY);
	AAudioStreamBuilder_setSharingMode(builder, AAUDIO_SHARING_MODE_EXCLUSIVE);
	AAudioStreamBuilder_setFormat(builder, AAUDIO_FORMAT_PCM_I16);

	AAudioStreamBuilder_setDataCallback(builder, &speaker_data_cb, this);
	AAudioStreamBuilder_setErrorCallback(builder, &speaker_error_cb, this);

	aaudio_result_t result = AAudioStreamBuilder_openStream(builder, &speaker);
	if (result != AAUDIO_OK)
		spdlog::error("Cannot create output stream: {}", AAudio_convertResultToText(result));

	if (result != AAUDIO_OK)
	{
		speaker = nullptr;
		return;
	}
	result = AAudioStream_requestStart(speaker);
	if (result == AAUDIO_OK)
		spdlog::info("Speaker stream started");
	else
	{
		AAudioStream_close(speaker);
		speaker = nullptr;
		spdlog::warn("Speaker stream failed to start: {}", AAudio_convertResultToText(result));
	}
}

void wivrn::android::audio::speaker_error_cb(AAudioStream *, void * userdata, aaudio_result_t error)
{
	auto self = static_cast<audio *>(userdata);
	self->speaker_error = error;
	self->wake.notify_one();
}

void wivrn::android::audio::microphone_error_cb(AAudioStream *, void * userdata, aaudio_result_t error)
{
	auto self = static_cast<audio *>(userdata);
	self->mic_error = error;
	self->wake.notify_one();
}

wivrn::android::audio::audio(const wivrn::to_headset::audio_stream_description & desc, wivrn_session & session, xr::instance & instance) :
        description(desc), session(session), instance(instance)
{
	worker = utils::named_thread("audio_owner", &audio::audio_worker, this);
}

wivrn::android::audio::~audio()
{
	exiting = true;
	wake.notify_one();
	// No callback acknowledgement wait and no join under the command mutex.
	if (worker.joinable())
		worker.join();
}

void wivrn::android::audio::operator()(wivrn::audio_data && data)
{
	auto size = data.payload.size_bytes();
	buffer_size_bytes += size;
	if (!output_buffer.write(std::move(data)))
		buffer_size_bytes -= size;
}

void wivrn::android::audio::set_mic_state(bool running)
{
	mic_requested = running;
	wake.notify_one();
}

void wivrn::android::audio::restart_microphone()
{
	mic_restart = true;
	wake.notify_one();
}

void wivrn::android::audio::send_microphone_data()
{
	for (unsigned i = 0; i < 32; ++i)
	{
		auto block = microphone_samples.read();
		if (!block)
			break;
		// Drop stale data after a stop/reopen, or after network backpressure.
		if (!mic_requested || block->generation != microphone_generation || instance.now() - block->timestamp > 100'000'000)
			continue;
		session.send_control(wivrn::audio_data{.timestamp = block->timestamp, .payload = std::span(block->data.data(), block->size)});
	}
}

void wivrn::android::audio::audio_worker()
{
	using clock = std::chrono::steady_clock;
	using namespace std::chrono_literals;
	utils::microphone_watchdog watchdog;
	bool mic_started = false, mic_fault = false, exhausted_logged = false, previous_requested = false;
	unsigned speaker_attempts = 0;
	auto speaker_retry = clock::now(), diagnostic = clock::now();
	auto close_stream = [](AAudioStream *& stream) {
		if (!stream)
			return;
		auto result = AAudioStream_requestStop(stream);
		if (result != AAUDIO_OK && result != AAUDIO_ERROR_DISCONNECTED)
			spdlog::warn("Audio stop: {}", AAudio_convertResultToText(result));
		AAudioStream_close(stream);
		stream = nullptr;
	};
	auto open_stream = [&](bool mic) {
		AAudioStreamBuilder * builder = nullptr;
		auto result = AAudio_createStreamBuilder(&builder);
		if (result != AAUDIO_OK)
		{
			spdlog::warn("Audio builder: {}", AAudio_convertResultToText(result));
			return;
		}
		if (mic)
			build_microphone(builder, description.microphone->sample_rate, description.microphone->num_channels);
		else
			build_speaker(builder, description.speaker->sample_rate, description.speaker->num_channels);
		AAudioStreamBuilder_delete(builder);
	};
	while (!exiting)
	{
		auto now = clock::now();
		if (auto error = speaker_error.exchange(0))
		{
			spdlog::warn("Speaker interrupted: {}", AAudio_convertResultToText(error));
			close_stream(speaker);
		}
		if (description.speaker && !speaker && speaker_attempts < 3 && now >= speaker_retry)
		{
			++speaker_attempts;
			speaker_retry = now + 1s;
			open_stream(false);
		}
		if (speaker && now - speaker_retry >= 10s)
			speaker_attempts = 0;

		bool requested = mic_requested && description.microphone.has_value();
		bool manual = mic_restart.exchange(false);
		if ((requested && !previous_requested) || manual)
		{
			watchdog = {};
			exhausted_logged = false;
			if (manual)
			{
				mic_fault = true;
				spdlog::info("Manual microphone recovery requested");
			}
		}
		previous_requested = requested;
		if (auto error = mic_error.exchange(0))
		{
			spdlog::warn("Microphone interrupted: {}", AAudio_convertResultToText(error));
			mic_fault = true;
		}
		if (!requested)
		{
			if (microphone)
			{
				++microphone_generation;
				close_stream(microphone);
			}
			mic_started = false;
			mic_fault = false;
		}
		else
		{
			if (microphone && mic_started)
			{
				int64_t position = 0, timestamp = 0;
				auto state = AAudioStream_getState(microphone);
				auto result = AAudioStream_getTimestamp(microphone, CLOCK_MONOTONIC, &position, &timestamp);
				std::optional<int64_t> hardware_position;
				if (result == AAUDIO_OK)
					hardware_position = position;
				if (watchdog.stalled(now, microphone_frames, hardware_position) || state == AAUDIO_STREAM_STATE_DISCONNECTED || state == AAUDIO_STREAM_STATE_STOPPED)
					mic_fault = true;
				if (now >= diagnostic || mic_fault)
				{
					spdlog::info("Microphone progress: state={} callbacks={} timestamp_result={} position={} timestamp_ns={} callback_age_ms={} hardware_age_ms={} drops={} retries={}",
					             AAudio_convertStreamStateToText(state),
					             microphone_frames.load(),
					             AAudio_convertResultToText(result),
					             position,
					             timestamp,
					             std::chrono::duration_cast<std::chrono::milliseconds>(now - watchdog.callback_progress).count(),
					             std::chrono::duration_cast<std::chrono::milliseconds>(now - watchdog.timestamp_progress).count(),
					             microphone_drops.load(),
					             watchdog.attempts);
					diagnostic = now + 5s;
				}
			}
			if (mic_fault && microphone)
			{
				++microphone_generation;
				close_stream(microphone);
				mic_started = false;
			}
			if (!microphone && (!mic_fault || watchdog.retry(now)))
			{
				if (mic_fault)
					spdlog::warn("Reopening microphone, bounded attempt {}/3", watchdog.attempts);
				++microphone_generation;
				mic_error = 0; // close completed; ignore errors from the retired stream
				open_stream(true);
				mic_fault = !microphone;
				if (microphone)
				{
					auto result = AAudioStream_requestStart(microphone);
					mic_started = result == AAUDIO_OK;
					if (!mic_started)
					{
						spdlog::warn("Microphone start: {}", AAudio_convertResultToText(result));
						close_stream(microphone);
						mic_fault = true;
					}
					else
					{
						watchdog.start(now, microphone_frames);
						spdlog::info("Microphone capture requested");
					}
				}
			}
			if (!microphone && watchdog.attempts >= 3 && !exhausted_logged)
			{
				spdlog::error("Microphone recovery exhausted; use Restart microphone in Audio settings");
				exhausted_logged = true;
			}
		}
		std::unique_lock lock(mutex);
		wake.wait_for(lock, 50ms);
	}
	close_stream(microphone);
	close_stream(speaker);
}

void wivrn::android::audio::get_audio_description(wivrn::from_headset::headset_info_packet & info)
{
	AAudioStreamBuilder * builder;

	aaudio_result_t result = AAudio_createStreamBuilder(&builder);
	if (result != AAUDIO_OK)
		return;

	AAudioStreamBuilder_setDirection(builder, AAUDIO_DIRECTION_OUTPUT);
	AAudioStream * stream;
	result = AAudioStreamBuilder_openStream(builder, &stream);

	if (result == AAUDIO_OK)
	{
		info.speaker = {
		        .num_channels = (uint8_t)AAudioStream_getChannelCount(stream),
		        .sample_rate = (uint32_t)AAudioStream_getSampleRate(stream)};

		AAudioStream_close(stream);
	}

	AAudioStreamBuilder_setDirection(builder, AAUDIO_DIRECTION_INPUT);

	AAudioStreamBuilder_setSampleRate(builder, 48'000);
	result = AAudioStreamBuilder_openStream(builder, &stream);

	if (result != AAUDIO_OK)
	{
		AAudioStreamBuilder_setSampleRate(builder, AAUDIO_UNSPECIFIED);
		result = AAudioStreamBuilder_openStream(builder, &stream);
	}

	if (result == AAUDIO_OK)
	{
		info.microphone = {
		        .num_channels = 1, // Some headsets report 2 channels but then fail
		        .sample_rate = (uint32_t)AAudioStream_getSampleRate(stream),
		};

		AAudioStream_close(stream);
	}

	AAudioStreamBuilder_delete(builder);
}
