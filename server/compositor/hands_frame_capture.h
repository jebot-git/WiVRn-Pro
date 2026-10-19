// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "utils/wivrn_vk_bundle.h"
#include "vk/allocation.h"
#include "xrt/xrt_defines.h"
#include <atomic>
#include <condition_variable>
#include <filesystem>
#include <mutex>
#include <thread>
namespace wivrn
{
// One bounded GPU readback, then a worker writes the private CPU frame. Busy
// consumers drop captures; neither file I/O nor GPU waits run on the render path.
class hands_frame_capture
{
	std::filesystem::path directory;
	buffer_allocation buffer;
	vk::raii::Device * device = nullptr;
	uint64_t pending = 0, last_capture = 0;
	std::atomic<bool> requested{false};
	bool failed = false;
	uint32_t width = 0, height = 0;
	xrt_fov fov{};
	uint64_t timestamp = 0;
	std::atomic<bool> busy{false};
	std::mutex mutex;
	std::condition_variable condition;
	bool job = false, stop = false;
	std::jthread worker;
	void run();

public:
	hands_frame_capture();
	~hands_frame_capture();
	bool enabled();
	void poll(vk::raii::Device &, const vk::raii::Semaphore &);
	void record(vk::raii::Device &, vk::CommandBuffer, vk::Image, vk::Extent3D, xrt_fov, uint64_t signal);
};
} // namespace wivrn
