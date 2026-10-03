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

#include "driver/clock_offset.h"
#include "idr_handler.h"
#include "recovery_policy.h"
#include "slot_ring.h"
#include "wivrn_packets.h"

#include <atomic>
#include <condition_variable>
#include <cstdint>
#include <deque>
#include <fstream>
#include <memory>
#include <mutex>
#include <shared_mutex>
#include <thread>
#include <vulkan/vulkan_raii.hpp>

namespace wivrn
{

struct encoder_settings;
struct vk_bundle;
class wivrn_session;

inline const char * encoder_qsv = "qsv";
inline const char * encoder_vaapi = "vaapi";
inline const char * encoder_vulkan = "vulkan";

class encoder_reconfiguration : public std::exception
{
	const char * what() const noexcept override
	{
		return "encoder rate change";
	}
};

class video_encoder
{
protected:
	struct data
	{
		video_encoder * encoder;
		std::span<uint8_t> span;
		std::shared_ptr<void> mem;
		// true if data should be sent over reliable (TCP) socket
		bool prefer_control = false;
	};

private:
	class sender
	{
		std::mutex mutex;
		std::condition_variable cv;
		std::deque<data> pending;
		std::jthread thread;
		void run();
		sender();

	public:
		void push(data &&);
		static std::shared_ptr<sender> get();
		bool wait_idle(video_encoder *, std::chrono::milliseconds timeout = std::chrono::milliseconds::zero());
	};

public:
	const uint8_t stream_idx;
	const uint32_t target_queue;
	const bool need_transfer;
	// Layout the encoder needs y_cbcr to arrive in. Set during construction
	// or init() and read by the compositor on every layer_commit — must not
	// change after the first frame.
	vk::ImageLayout target_layout = vk::ImageLayout::eGeneral;
	static const uint8_t num_slots = 2;
	const double bitrate_multiplier;

private:
	slot_ring<num_slots> slots;

	std::mutex mutex;
	std::shared_mutex operation_mutex;
	recovery_policy recovery;
	std::atomic_bool recovery_requested = false;
	std::atomic_bool manual_recovery = false;
	std::atomic_bool resumed = false;
	std::atomic_bool reconfigure_requested = false;
	std::atomic<int64_t> last_demand = 0;
	std::atomic<uint64_t> submitted_frame = 0;
	std::atomic<uint64_t> encoded_frame = 0;
	std::atomic<uint64_t> sent_frame = 0;
	bool frame_send_failed = false;
	std::atomic<uint64_t> decoded_frame = 0;
	uint64_t observed_decoded_frame = 0;

	// temporary data
	wivrn_session * cnx;

	// shard to send
	to_headset::video_stream_data_shard shard;

	to_headset::video_stream_data_shard::timing_info_t timing_info;
	clock_offset clock;

	std::ofstream video_dump;

	std::shared_ptr<sender> shared_sender;
	std::optional<data> staged_output;

protected:
	std::atomic_uint32_t pending_bitrate{0};
	std::atomic<float> pending_framerate{0};
	std::unique_ptr<idr_handler> idr;
	const vk::Extent2D extent;

public:
	static std::unique_ptr<video_encoder> create(
	        wivrn::vk_bundle &,
	        const encoder_settings & settings,
	        uint8_t stream_idx);

	video_encoder(vk_bundle &, uint8_t stream_idx, uint32_t target_queue, const encoder_settings & settings, std::unique_ptr<idr_handler>, bool async_send);
	virtual ~video_encoder();

	bool present_image(vk::Image y_cbcr,
	                   vk::SemaphoreSubmitInfo sem_info,
	                   uint64_t frame_index);

	void on_feedback(const from_headset::feedback &);
	void reset();
	void request_recovery(bool manual = false);
	// Called only on the encoder thread. Returns true after codec recreation.
	bool service_recovery(bool connected, bool expected);
	std::string recovery_status();
	bool drain_sender();
	bool ready_for_present();
	void commit_encoded(bool keep);

	// bitrate_bps is the bitrate for the whole stream
	// the encoder bitrate will be scaled accordingly
	void set_bitrate(uint32_t bitrate_bps);
	void set_framerate(float framerate);

	void encode(wivrn_session & cnx,
	            const to_headset::video_stream_data_shard::view_info_t & view_info,
	            uint64_t frame_index);

protected:
	// Codec recreation must keep imported images and slot indices intact.
	void request_reconfigure()
	{
		reconfigure_requested = true;
	}
	virtual bool recover(unsigned attempt)
	{
		return false;
	}
	virtual const char * backend_name() const
	{
		return "encoder";
	}
	virtual bool supports_recovery() const
	{
		return false;
	}

	// called on present to submit command buffers for the image.
	virtual void present_image(vk::Image y_cbcr,
	                           vk::SemaphoreSubmitInfo sem_info,
	                           uint8_t slot,
	                           uint64_t frame_index) = 0;

	// called when command buffer finished executing
	virtual std::optional<data> encode(uint8_t slot, uint64_t frame_index) = 0;

	void SendData(std::span<uint8_t> data, bool end_of_frame, bool control = false);
};

} // namespace wivrn
