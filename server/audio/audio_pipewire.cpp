/*
 * WiVRn VR streaming
 * Copyright (C) 2024  Patrick Nicolas <patricknicolas@laposte.net>
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

#include "audio_pipewire.h"

#include "driver/wivrn_session.h"
#include "os/os_time.h"
#include "util/u_logging.h"
#include "utils/audio_recovery.h"
#include "utils/ring_buffer.h"
#include <magic_enum.hpp>
#include <memory>
#include <pipewire/pipewire.h>
#include <spa/param/audio/format-utils.h>

namespace wivrn
{

namespace
{
struct deleter
{
	void operator()(pw_main_loop * loop)
	{
		pw_main_loop_destroy(loop);
	}
	void operator()(pw_stream * stream)
	{
		pw_stream_destroy(stream);
	}
};

struct pipewire_device : public audio_device
{
	to_headset::audio_stream_description desc;
	wivrn_session & session;

	std::unique_ptr<pw_main_loop, deleter> pw_loop;

	std::unique_ptr<pw_stream, deleter> speaker;
	pw_stream_events speaker_events{
	        .version = PW_VERSION_STREAM_EVENTS,
	        .process = &pipewire_device::speaker_process,
	};

	struct mic_packet
	{
		audio_data data;
		uint64_t generation;
	};
	utils::ring_buffer<mic_packet, 100> mic_samples;
	std::atomic<uint64_t> mic_generation = 0;
	uint64_t current_generation = 0;
	utils::microphone_idle_hold mic_hold;
	std::atomic<bool> mic_enabled = false;
	spa_source * mic_idle_timer = nullptr;
	std::atomic<size_t> mic_buffer_size_bytes = 0;
	audio_data mic_current;
	std::unique_ptr<pw_stream, deleter> microphone;
	std::atomic<std::underlying_type_t<pw_stream_state>> mic_state{PW_STREAM_STATE_UNCONNECTED};
	pw_stream_events mic_events{
	        .version = PW_VERSION_STREAM_EVENTS,
	        .state_changed = &pipewire_device::mic_state_changed,
	        .process = &pipewire_device::mic_process,
	};
	std::jthread thread;

	void set_mic_enabled(bool enabled);
	static void mic_idle_expired(void * self_v, uint64_t);
	static void speaker_process(void * self_v);
	static void mic_process(void * self_v);
	static void mic_state_changed(void * self_v, pw_stream_state old, pw_stream_state state, const char * error);

	void process_mic_data(wivrn::audio_data &&) override;
	void pause() override;
	void resume() override;

	~pipewire_device()
	{
		pw_main_loop_quit(pw_loop.get());
	};

	pipewire_device(
	        const std::string & source_name,
	        const std::string & source_description,
	        const std::string & sink_name,
	        const std::string & sink_description,
	        const wivrn::from_headset::headset_info_packet & info,
	        wivrn::wivrn_session & session) :
	        session(session)
	{
		int argc = 0;
		pw_init(&argc, nullptr);

		pw_loop.reset(pw_main_loop_new(nullptr));
		mic_idle_timer = pw_loop_add_timer(pw_main_loop_get_loop(pw_loop.get()), &mic_idle_expired, this);
		if (!mic_idle_timer)
			throw std::runtime_error("Cannot create microphone idle timer");
		if (info.speaker)
		{
			desc.speaker = {
			        .num_channels = info.speaker->num_channels,
			        .sample_rate = info.speaker->sample_rate,
			};

			// Calculate quantum size: 5ms buffer for low latency while maintaining stability
			// Smaller buffers (<5ms) risk underruns, larger ones (>10ms) add perceptible latency
			uint32_t quantum_size = (desc.speaker->sample_rate * 5) / 1000;
			uint32_t frame_size = desc.speaker->num_channels * sizeof(int16_t);

			std::string rate_str = std::format("1/{}", desc.speaker->sample_rate);
			std::string latency_str = std::format("{}/{}", quantum_size, desc.speaker->sample_rate);

			speaker.reset(pw_stream_new_simple(
			        pw_main_loop_get_loop(pw_loop.get()),
			        sink_name.c_str(),
			        pw_properties_new(
			                PW_KEY_NODE_NAME,
			                sink_name.c_str(),
			                PW_KEY_NODE_DESCRIPTION,
			                sink_description.c_str(),
			                PW_KEY_MEDIA_TYPE,
			                "Audio",
			                PW_KEY_MEDIA_CATEGORY,
			                "Capture",
			                PW_KEY_MEDIA_CLASS,
			                "Audio/Sink",
			                PW_KEY_MEDIA_ROLE,
			                "Game",
			                // Set stream rate to match client, preventing PipeWire from doing
			                // unnecessary resampling which degrades audio quality
			                PW_KEY_NODE_RATE,
			                rate_str.c_str(),
			                // Declare target latency to help PipeWire optimize buffering
			                PW_KEY_NODE_LATENCY,
			                latency_str.c_str(),
			                NULL),
			        &speaker_events,
			        this));

			std::vector<uint8_t> buffer(1024);
			spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer.data(), uint32_t(buffer.size()));

			spa_audio_info_raw audio_info{
			        .format = SPA_AUDIO_FORMAT_S16,
			        .rate = desc.speaker->sample_rate,
			        .channels = desc.speaker->num_channels,
			};

			switch (audio_info.channels)
			{
				case 1:
					audio_info.position[0] = SPA_AUDIO_CHANNEL_MONO;
					break;
				case 2:
					audio_info.position[0] = SPA_AUDIO_CHANNEL_FL;
					audio_info.position[1] = SPA_AUDIO_CHANNEL_FR;
					break;
				default:
					U_LOG_W("No known audio mapping for %d channels speaker", audio_info.channels);
			}

			const spa_pod * params[1];
			params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat, &audio_info);

			// Stream flags:
			// - DRIVER: makes this node the graph clock master, preventing sync drift in the audio chain
			if (pw_stream_connect(
			            speaker.get(),
			            PW_DIRECTION_INPUT,
			            PW_ID_ANY,
			            pw_stream_flags(PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS | PW_STREAM_FLAG_RT_PROCESS),
			            params,
			            1) < 0)
				throw std::runtime_error("failed to connect speaker stream");
			U_LOG_I("pipewire speaker stream created (quantum: %u frames, %.2f ms)", quantum_size, (quantum_size * 1000.0) / desc.speaker->sample_rate);
		}

		if (info.microphone)
		{
			desc.microphone = {
			        .num_channels = info.microphone->num_channels,
			        .sample_rate = info.microphone->sample_rate,
			};

			// Calculate quantum size: 5ms buffer for low latency while maintaining stability
			// Smaller buffers (<5ms) risk underruns, larger ones (>10ms) add perceptible latency
			uint32_t quantum_size = (desc.microphone->sample_rate * 5) / 1000;
			uint32_t frame_size = desc.microphone->num_channels * sizeof(int16_t);

			std::string rate_str = std::format("1/{}", desc.microphone->sample_rate);
			std::string latency_str = std::format("{}/{}", quantum_size, desc.microphone->sample_rate);

			microphone.reset(pw_stream_new_simple(
			        pw_main_loop_get_loop(pw_loop.get()),
			        source_name.c_str(),
			        pw_properties_new(
			                PW_KEY_NODE_NAME,
			                source_name.c_str(),
			                PW_KEY_NODE_DESCRIPTION,
			                source_description.c_str(),
			                PW_KEY_MEDIA_TYPE,
			                "Audio",
			                PW_KEY_MEDIA_CATEGORY,
			                "Playback",
			                PW_KEY_MEDIA_CLASS,
			                "Audio/Source",
			                PW_KEY_MEDIA_ROLE,
			                "Game",
			                // Set stream rate to match client, preventing PipeWire from doing
			                // unnecessary resampling which degrades audio quality
			                PW_KEY_NODE_RATE,
			                rate_str.c_str(),
			                // Declare target latency to help PipeWire optimize buffering
			                PW_KEY_NODE_LATENCY,
			                latency_str.c_str(),
			                NULL),
			        &mic_events,
			        this));
			std::vector<uint8_t> buffer(1024);
			spa_pod_builder b = SPA_POD_BUILDER_INIT(buffer.data(), uint32_t(buffer.size()));

			spa_audio_info_raw audio_info{
			        .format = SPA_AUDIO_FORMAT_S16,
			        .rate = desc.microphone->sample_rate,
			        .channels = desc.microphone->num_channels,
			};

			switch (audio_info.channels)
			{
				case 1:
					audio_info.position[0] = SPA_AUDIO_CHANNEL_MONO;
					break;
				case 2:
					audio_info.position[0] = SPA_AUDIO_CHANNEL_FL;
					audio_info.position[1] = SPA_AUDIO_CHANNEL_FR;
					break;
				default:
					U_LOG_W("No known audio mapping for %d channels microphone", audio_info.channels);
			}

			const spa_pod * params[1];
			params[0] = spa_format_audio_raw_build(&b, SPA_PARAM_EnumFormat, &audio_info);

			if (pw_stream_connect(
			            microphone.get(),
			            PW_DIRECTION_OUTPUT,
			            PW_ID_ANY,
			            pw_stream_flags(PW_STREAM_FLAG_AUTOCONNECT | PW_STREAM_FLAG_MAP_BUFFERS),
			            params,
			            1) < 0)
				throw std::runtime_error("failed to connect microphone stream");
			U_LOG_I("pipewire microphone stream created (quantum: %u frames, %.2f ms)", quantum_size, (quantum_size * 1000.0) / desc.microphone->sample_rate);
		}

		if (desc.speaker or desc.microphone)
			thread = std::jthread(
			        [this](std::stop_token) {
				pw_main_loop_run(pw_loop.get());
				speaker.reset();
				microphone.reset();
				pw_loop_destroy_source(pw_main_loop_get_loop(pw_loop.get()), mic_idle_timer);
				mic_idle_timer = nullptr; });
	}
};
} // namespace

void pipewire_device::mic_process(void * self_v)
{
	// std::cerr << "mic_process" << std::endl;
	auto self = (pipewire_device *)self_v;
	auto buffer = pw_stream_dequeue_buffer(self->microphone.get());
	if (not buffer)
	{
		U_LOG_W("Out of buffers: %s", strerror(errno));
		return;
	}

	const auto & data = buffer->buffer->datas[0];
	uint8_t * data_ptr = (uint8_t *)data.data;
	if (not data.data)
	{
		pw_stream_queue_buffer(self->microphone.get(), buffer);
		return;
	}

	const auto generation = self->mic_generation.load();
	if (self->current_generation != generation)
	{
		self->mic_buffer_size_bytes -= self->mic_current.payload.size_bytes();
		self->mic_current = {};
		self->current_generation = generation;
	}
	const size_t frame_size = self->desc.microphone->num_channels * sizeof(int16_t);

	// Use consistent buffer size based on stream quantum
#if PW_CHECK_VERSION(0, 3, 49)
	size_t num_frames = buffer->requested;
#else
	size_t num_frames = 0;
#endif
	if (num_frames == 0)
	{
		uint32_t quantum_size = (self->desc.microphone->sample_rate * 5) / 1000;
		num_frames = std::min<size_t>(quantum_size, data.maxsize / frame_size);
	}
	data.chunk->offset = 0;
	data.chunk->size = 0;
	data.chunk->stride = frame_size;

	while (num_frames != 0)
	{
		// remaining bytes in existing buffer
		auto & current = self->mic_current;
		ptrdiff_t tmp_remain = current.payload.size_bytes();
		// limit to requested frames
		tmp_remain = std::min<ptrdiff_t>(tmp_remain, num_frames * frame_size);
		if (tmp_remain)
		{
			memcpy(data_ptr, current.payload.data(), tmp_remain);
			current.payload = current.payload.subspan(tmp_remain);
			data_ptr += tmp_remain;
			data.chunk->size += tmp_remain;
			num_frames -= tmp_remain / frame_size;
			self->mic_buffer_size_bytes -= tmp_remain;
		}
		else
		{
			auto tmp = self->mic_samples.read();
			if (not tmp)
				break;
			if (tmp->generation != generation)
			{
				self->mic_buffer_size_bytes -= tmp->data.payload.size_bytes();
				continue;
			}
			self->mic_current = std::move(tmp->data);
		}
	}
	pw_stream_queue_buffer(self->microphone.get(), buffer);

	// discard excess data, so we don't accumulate latency
	size_t target_buffer_size = frame_size * self->desc.microphone->sample_rate * 0.08;
	while (self->mic_buffer_size_bytes > target_buffer_size and self->mic_samples.size() > 1)
	{
		auto tmp = self->mic_samples.read();
		if (not tmp)
			break;
		self->mic_buffer_size_bytes -= tmp->data.payload.size_bytes();
		U_LOG_D("Audio sync: discard %zd bytes", tmp->data.payload.size_bytes());
	}
}

void pipewire_device::set_mic_enabled(bool enabled)
{
	if (mic_enabled.exchange(enabled) == enabled)
		return;
	try
	{
		session.send_control(to_headset::feature_control{to_headset::feature_control::microphone, enabled});
	}
	catch (std::exception & e)
	{
		U_LOG_W("failed to update microphone state: %s", e.what());
	}
}

void pipewire_device::mic_idle_expired(void * self_v, uint64_t)
{
	auto self = static_cast<pipewire_device *>(self_v);
	if (self->mic_state == PW_STREAM_STATE_PAUSED)
	{
		U_LOG_I("Microphone scene-load grace expired");
		self->set_mic_enabled(self->mic_hold.update(false, true, utils::microphone_idle_hold::clock::now()));
	}
}

void pipewire_device::mic_state_changed(void * self_v, pw_stream_state old, pw_stream_state state, const char * error)
{
	auto self = static_cast<pipewire_device *>(self_v);
	// Tag stale samples; only mic_process consumes the SPSC queue.
	++self->mic_generation;
	self->mic_state = state;
	U_LOG_I("Microphone state changed from %s to %s%s%s", magic_enum::enum_name(old).data(), magic_enum::enum_name(state).data(), error ? ": " : "", error ? error : "");
	auto now = utils::microphone_idle_hold::clock::now();
	self->set_mic_enabled(self->mic_hold.update(state == PW_STREAM_STATE_STREAMING, state == PW_STREAM_STATE_PAUSED, now));
	timespec timeout{};
	if (self->mic_hold.deadline)
	{
		timeout.tv_sec = 30;
		U_LOG_I("Keeping microphone capture alive for up to 30 seconds during reader idle");
	}
	pw_loop_update_timer(pw_main_loop_get_loop(self->pw_loop.get()), self->mic_idle_timer, &timeout, nullptr, false);
}

void pipewire_device::speaker_process(void * self_v)
{
	auto self = (pipewire_device *)self_v;
	auto buffer = pw_stream_dequeue_buffer(self->speaker.get());
	if (not buffer)
	{
		U_LOG_W("Out of buffers: %s", strerror(errno));
		return;
	}

	const auto & data = buffer->buffer->datas[0];
	if (not data.data)
		return;

	try
	{
		self->session.send_control(audio_data{
		        .timestamp = self->session.get_offset().to_headset(os_monotonic_get_ns()),
		        .payload = std::span(
		                (uint8_t *)data.data + data.chunk->offset,
		                data.chunk->size),
		});
	}
	catch (std::exception & e)
	{
		U_LOG_D("Failed to send audio data: %s", e.what());
	}
	pw_stream_queue_buffer(self->speaker.get(), buffer);
}

void pipewire_device::process_mic_data(wivrn::audio_data && sample)
{
	const auto generation = mic_generation.load();
	if (mic_state != PW_STREAM_STATE_STREAMING)
		return;
	auto size = sample.payload.size_bytes();
	mic_buffer_size_bytes += size;
	if (!mic_samples.write(mic_packet{std::move(sample), generation}))
		mic_buffer_size_bytes -= size;
}

void pipewire_device::pause()
{
}

void pipewire_device::resume()
{
	session.send_control(to_headset::audio_stream_description{desc});
	session.send_control(to_headset::feature_control{to_headset::feature_control::microphone, mic_enabled.load()});
}

std::unique_ptr<audio_device> create_pipewire_handle(
        const std::string & source_name,
        const std::string & source_description,
        const std::string & sink_name,
        const std::string & sink_description,
        const wivrn::from_headset::headset_info_packet & info,
        wivrn_session & session)
{
	try
	{
		return std::make_unique<pipewire_device>(
		        source_name, source_description, sink_name, sink_description, info, session);
	}
	catch (std::exception & e)
	{
		U_LOG_I("Pipewire backend creation failed: %s", e.what());
		return nullptr;
	}
}
} // namespace wivrn
