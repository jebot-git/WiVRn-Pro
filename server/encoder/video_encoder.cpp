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

// Include first because of incompatibility between Eigen and X includes
#include "driver/wivrn_session.h"

#include "video_encoder.h"

#include "encoder_settings.h"
#include "os/os_time.h"
#include "utils/wivrn_trace.h"
#include "wivrn_config.h"

#include <cmath>
#include <string>

#if WIVRN_USE_VAAPI || WIVRN_USE_QSV
#include "ffmpeg/video_encoder_va.h"
#endif
#if WIVRN_USE_VULKAN_ENCODE
#include "video_encoder_vulkan_h264.h"
#include "video_encoder_vulkan_h265.h"
#endif

namespace wivrn
{

video_encoder::sender::sender() :
        thread([this](std::stop_token t) {
	        while (not t.stop_requested())
	        {
		        data * d = nullptr;
		        {
			        std::unique_lock lock(mutex);
			        if (pending.empty())
				        cv.wait_for(lock, std::chrono::milliseconds(100));
			        else
				        d = &pending.front();
		        }
		        if (d)
		        {
			        try
			        {
				        if (!d->span.empty())
					        d->encoder->SendData(d->span, true, d->prefer_control);
			        }
			        catch (const std::exception & e)
			        {
				        U_LOG_W("Video send failed: %s", e.what());
				        d->encoder->reset();
			        }
			        std::unique_lock lock(mutex);
			        pending.pop_front();
			        cv.notify_all();
		        }
	        }
	        std::unique_lock lock(mutex);
	        pending.clear();
	        cv.notify_all();
        })
{
}

void video_encoder::sender::push(data && d)
{
	std::unique_lock lock(mutex);
	pending.push_back(std::move(d));
	cv.notify_all();
}

bool video_encoder::sender::wait_idle(video_encoder * encoder, std::chrono::milliseconds timeout)
{
	std::unique_lock lock(mutex);
	auto idle = [&] { return std::ranges::none_of(pending, [=](auto & data) { return data.encoder == encoder; }); };
	if (timeout == std::chrono::milliseconds::zero())
	{
		cv.wait(lock, idle);
		return true;
	}
	return cv.wait_for(lock, timeout, idle);
}

std::shared_ptr<video_encoder::sender> video_encoder::sender::get()
{
	static std::weak_ptr<video_encoder::sender> instance;
	static std::mutex m;
	std::unique_lock lock(m);
	auto s = instance.lock();
	if (s)
		return s;
	s.reset(new video_encoder::sender());
	instance = s;
	return s;
}

std::unique_ptr<video_encoder> video_encoder::create(
        wivrn::vk_bundle & wivrn_vk,
        const encoder_settings & settings,
        uint8_t stream_idx)
{
	using namespace std::string_literals;
	std::unique_ptr<video_encoder> res;
	if (settings.encoder_name == encoder_vulkan)
	{
#if WIVRN_USE_VULKAN_ENCODE
		switch (settings.codec)
		{
			case video_codec::h264:
				res = video_encoder_vulkan_h264::create(wivrn_vk, settings, stream_idx);
				break;
			case video_codec::h265:
				res = video_encoder_vulkan_h265::create(wivrn_vk, settings, stream_idx);
				break;
			case video_codec::av1:
				throw std::runtime_error("av1 not supported for vulkan video encode");
			case video_codec::raw:
				throw std::runtime_error("raw codec only supported on raw encoder");
		}
#else
		throw std::runtime_error("Vulkan video encode not enabled");
#endif
	}
	if (settings.encoder_name == encoder_vaapi || settings.encoder_name == encoder_qsv)
	{
#if WIVRN_USE_VAAPI || WIVRN_USE_QSV
		res = std::make_unique<video_encoder_va>(wivrn_vk, settings, stream_idx);
#else
		throw std::runtime_error("vaapi support not enabled");
#endif
	}

	if (not res)
		throw std::runtime_error("Failed to create encoder " + settings.encoder_name);

	auto wivrn_dump_video = std::getenv("WIVRN_DUMP_VIDEO");
	if (wivrn_dump_video)
	{
		std::string file(wivrn_dump_video);
		file += "-" + std::to_string(stream_idx);
		switch (settings.codec)
		{
			case h264:
				file += ".h264";
				break;
			case h265:
				file += ".h265";
				break;
			case av1:
				file += ".av1";
				break;
			case raw:
				file += ".yuv";
				break;
		}
		res->video_dump.open(file);
	}
	return res;
}

video_encoder::video_encoder(vk_bundle & vk,
                             uint8_t stream_idx,
                             uint32_t target_queue,
                             const encoder_settings & settings,
                             std::unique_ptr<idr_handler> idr,
                             bool async_send) :
        stream_idx(stream_idx),
        target_queue(target_queue),
        need_transfer(not vk.optimal_transfer(vk.queue.family_index, target_queue)),
        bitrate_multiplier(settings.bitrate_multiplier),
        shared_sender(async_send ? sender::get() : nullptr),
        idr(std::move(idr)),
        extent{
                .width = settings.width,
                .height = settings.height,
        }
{
	assert(this->idr);
}

video_encoder::~video_encoder()
{
	if (shared_sender)
		shared_sender->wait_idle(this);
}

void video_encoder::on_feedback(const from_headset::feedback & feedback)
{
	if (feedback.received_from_decoder)
	{
		auto old = decoded_frame.load();
		while (old < feedback.frame_index && !decoded_frame.compare_exchange_weak(old, feedback.frame_index))
		{
		}
	}
	assert(feedback.stream_index == stream_idx);
	idr->on_feedback(feedback);
}

void video_encoder::reset()
{
	resumed = true;
	idr->reset();
}

void video_encoder::set_bitrate(uint32_t bitrate_bps)
{
	if (bitrate_bps > 0)
		pending_bitrate = std::clamp(bitrate_bps * bitrate_multiplier, 1.0, double(UINT32_MAX));
}

void video_encoder::set_framerate(float framerate)
{
	if (std::isfinite(framerate) && framerate >= 1 && framerate <= 1000)
		pending_framerate = framerate;
}

namespace
{
int64_t recovery_now()
{
	return std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
}
} // namespace

void video_encoder::request_recovery(bool manual)
{
	if (!supports_recovery())
	{
		idr->reset();
		return;
	}
	if (manual)
		manual_recovery = true;
	recovery_requested = true;
}

bool video_encoder::service_recovery(bool connected, bool expected)
{
	std::unique_lock lock(operation_mutex);
	const auto now = recovery_now();
	if (resumed.exchange(false))
	{
		recovery.resume(now);
		observed_decoded_frame = decoded_frame.load();
	}
	const auto decoded = decoded_frame.load();
	if (decoded > observed_decoded_frame)
	{
		recovery.decoded(now, decoded);
		observed_decoded_frame = decoded;
	}
	if (!supports_recovery())
		return false;
	if (connected && reconfigure_requested.exchange(false) && !recovery.blocked())
	{
		// A planned rate change does not consume the hardware-failure retry budget.
		try
		{
			if (recover(0))
			{
				recovery.resume(now);
				recovery.status = recovery_policy::state::awaiting_decode;
				idr->reset();
				return true;
			}
		}
		catch (const std::exception & e)
		{
			U_LOG_W("Encoder reconfiguration failed: %s", e.what());
		}
		recovery.request(now);
	}
	if (recovery_requested.exchange(false))
		recovery.request(now, manual_recovery.exchange(false));
	recovery.tick(now, connected, expected && now - last_demand.load() < 500);
	if (!recovery.due(now, connected) || !recovery.begin())
		return false;
	U_LOG_W("Video recovery stream %u: %s attempt %u (submitted %llu, encoded %llu, sent %llu, decoded %llu)",
	        stream_idx,
	        backend_name(),
	        recovery.attempts,
	        (unsigned long long)submitted_frame.load(),
	        (unsigned long long)encoded_frame.load(),
	        (unsigned long long)sent_frame.load(),
	        (unsigned long long)decoded_frame.load());
	bool success = false;
	try
	{
		success = recover(recovery.attempts);
	}
	catch (const std::exception & e)
	{
		U_LOG_W("Video recovery stream %u failed: %s", stream_idx, e.what());
	}
	recovery.finished(recovery_now(), success);
	if (success)
		idr->reset();
	return success;
}

void video_encoder::commit_encoded(bool keep)
{
	if (staged_output)
	{
		if (keep)
			shared_sender->push(std::move(*staged_output));
		else
			idr->reset();
		staged_output.reset();
	}
}

bool video_encoder::ready_for_present()
{
	last_demand = recovery_now();
	std::shared_lock lock(operation_mutex);
	return !recovery.blocked() && !recovery_requested && !reconfigure_requested && slots.ready();
}

bool video_encoder::drain_sender()
{
	return !shared_sender || shared_sender->wait_idle(this, std::chrono::milliseconds(100));
}

std::string video_encoder::recovery_status()
{
	std::unique_lock lock(operation_mutex);
	return std::to_string(stream_idx) + ": " + backend_name() + " " + recovery.name();
}

bool video_encoder::present_image(vk::Image y_cbcr, vk::SemaphoreSubmitInfo info, uint64_t frame_index)
{
	last_demand = recovery_now();
	std::shared_lock lock(operation_mutex, std::try_to_lock);
	if (!lock || recovery.blocked() || recovery_requested || reconfigure_requested)
		return false;
	auto reserved = slots.reserve();
	if (!reserved)
		return false;
	const auto present_slot = *reserved;
	submitted_frame = frame_index;
	recovery.submitted(recovery_now(), frame_index);
	if (idr->should_skip(frame_index))
	{
		slots.skip_reserved(present_slot);
		return true;
	}
	try
	{
		present_image(y_cbcr, info, present_slot, frame_index);
	}
	catch (const std::exception & e)
	{
		// Consume this slot on the encoder thread even on partial submission.
		slots.skip_reserved(present_slot);
		request_recovery();
		U_LOG_W("Video submission failed on stream %u: %s", stream_idx, e.what());
	}
	return true;
}

void video_encoder::encode(wivrn_session & cnx,
                           const to_headset::video_stream_data_shard::view_info_t & view_info,
                           uint64_t frame_index)
{
	std::shared_lock operation_lock(operation_mutex);
	staged_output.reset();
	auto lease = slots.consume();
	const auto encode_slot = lease.slot;
	if (recovery.blocked() || recovery_requested)
		throw std::runtime_error("encoder recovery pending");
	if (lease.skipped)
		return;

	if (shared_sender && !shared_sender->wait_idle(this, std::chrono::milliseconds(100)))
		throw std::runtime_error("video sender stalled");
	this->cnx = &cnx;
	clock = cnx.get_offset();

	wivrn::trace::scope trace_encode(wivrn::trace::cpu_track::encoder, stream_idx, frame_index, "encode");
	auto encode_begin = os_monotonic_get_ns();
	timing_info = {
	        .encode_begin = clock.to_headset(encode_begin),
	};

	// Prepare the video shard template
	shard.stream_item_idx = stream_idx;
	frame_send_failed = false;
	shard.frame_idx = frame_index;
	shard.shard_idx = 0;
	shard.view_info = view_info;
	shard.timing_info.reset();

	auto data = encode(encode_slot, frame_index);
	if (data)
	{
		timing_info.encode_end = clock.to_headset(os_monotonic_get_ns());
		assert(shared_sender);
		encoded_frame = frame_index;
		staged_output = std::move(*data);
	}
}

void video_encoder::SendData(std::span<uint8_t> data, bool end_of_frame, bool control)
{
	std::lock_guard lock(mutex);
	if (shard.shard_idx == 0)
	{
		// One SendData call per NAL; span the whole frame, not each call.
		wivrn::trace::cpu_begin(wivrn::trace::cpu_track::network, stream_idx, shard.frame_idx, "SendData");
		timing_info.send_begin = clock.to_headset(os_monotonic_get_ns());
	}
	if (end_of_frame)
	{
		timing_info.send_end = clock.to_headset(os_monotonic_get_ns());
		if (not timing_info.encode_end)
			timing_info.encode_end = timing_info.send_end;
	}
	if (video_dump)
		video_dump.write((char *)data.data(), data.size());

	ssize_t max_payload_size = (cnx->has_stream() and not control) ? to_headset::video_stream_data_shard::max_payload_size : std::numeric_limits<uint32_t>::max();

	auto begin = data.begin();
	auto end = data.end();
	while (begin != end)
	{
		const size_t payload_size = std::max(0z, max_payload_size - ssize_t(serialized_size(shard.view_info)));
		auto next = std::min(end, begin + payload_size);
		if (next == end)
		{
			if (end_of_frame)
				shard.timing_info = timing_info;
		}
		shard.payload = {begin, next};
		try
		{
			if (control)
				cnx->send_control(to_headset::video_stream_data_shard{shard});
			else
				cnx->send_stream(to_headset::video_stream_data_shard{shard});
		}
		catch (...)
		{
			frame_send_failed = true;
			// The session network thread handles reconnects; do not count a failed send.
		}
		++shard.shard_idx;
		shard.view_info.reset();
		begin = next;
	}
	if (end_of_frame && !frame_send_failed)
		sent_frame = shard.frame_idx;
	if (end_of_frame)
		wivrn::trace::cpu_end(wivrn::trace::cpu_track::network, stream_idx, shard.frame_idx, "SendData");
}

} // namespace wivrn
