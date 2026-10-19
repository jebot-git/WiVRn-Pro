// SPDX-License-Identifier: GPL-3.0-or-later
#include "hands_frame_capture.h"
#include "hands_frame_transport.h"
#include "os/os_time.h"
#include <array>
#include <bit>
#include <cstdlib>
#include <cstring>
#include <fcntl.h>
#include <sys/file.h>
#include <sys/stat.h>
#include <unistd.h>
namespace wivrn
{
namespace
{
bool private_directory(const std::filesystem::path & path)
{
	struct stat s{};
	return !lstat(path.c_str(), &s) && S_ISDIR(s.st_mode) && s.st_uid == getuid() && !(s.st_mode & 077);
}
bool lease(const std::filesystem::path & path)
{
	int fd = open(path.c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
	if (fd < 0)
		return false;
	struct stat s{};
	uint64_t stamp = 0;
	auto now = os_monotonic_get_ns();
	bool valid = !fstat(fd, &s) && S_ISREG(s.st_mode) && s.st_uid == getuid() && !(s.st_mode & 077) && s.st_size == 8 && read(fd, &stamp, 8) == 8 && stamp <= uint64_t(now) && uint64_t(now) - stamp < 1'000'000'000;
	close(fd);
	return valid;
}
} // namespace
hands_frame_capture::hands_frame_capture()
{
	if (auto runtime = std::getenv("XDG_RUNTIME_DIR"))
		directory = std::filesystem::path(runtime) / "wivrn-pro";
	worker = std::jthread([this] { run(); });
}
hands_frame_capture::~hands_frame_capture()
{
	if (pending && device)
	{
		try
		{
			device->waitIdle();
		}
		catch (...)
		{}
	}
	{
		std::lock_guard lock(mutex);
		stop = true;
	}
	condition.notify_all();
	worker.join();
	if (!directory.empty())
		unlink((directory / "hands-frame.rgba").c_str());
}
bool hands_frame_capture::enabled()
{
	if (!requested.load())
	{
		failed = false;
		return false;
	}
	return !failed;
}
void hands_frame_capture::poll(vk::raii::Device &, const vk::raii::Semaphore & semaphore)
{
	if (!pending || busy || !enabled())
		return;
	try
	{
		if (semaphore.getCounterValue() < pending)
			return;
	}
	catch (...)
	{
		failed = true;
		return;
	}
	pending = 0;
	if (!enabled())
		return;
	vmaInvalidateAllocation(vk_allocator::instance(), buffer, 0, VK_WHOLE_SIZE);
	{
		std::lock_guard lock(mutex);
		busy = true;
		job = true;
	}
	condition.notify_one();
}
void hands_frame_capture::record(vk::raii::Device & dev, vk::CommandBuffer cmd, vk::Image image, vk::Extent3D extent, xrt_fov angles, uint64_t signal)
{
	auto now = uint64_t(os_monotonic_get_ns());
	if (!enabled() || pending || busy || now - last_capture < 66'666'666 || !extent.width || !extent.height ||
	    extent.width > 8192 || extent.height > 8192 || uint64_t(extent.width) * extent.height * 4 > 128 * 1024 * 1024)
		return;
	if (!buffer || extent.width != width || extent.height != height)
	{
		try
		{
			buffer = buffer_allocation(dev, {.size = uint64_t(extent.width) * extent.height * 4, .usage = vk::BufferUsageFlagBits::eTransferDst}, {.flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT, .usage = VMA_MEMORY_USAGE_AUTO, .requiredFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT}, "Hands Frame readback");
			buffer.map();
			width = extent.width;
			height = extent.height;
		}
		catch (...)
		{
			failed = true;
			return;
		}
	}
	device = &dev;
	fov = angles;
	timestamp = now;
	last_capture = now;
	vk::ImageMemoryBarrier barrier{.srcAccessMask = vk::AccessFlagBits::eShaderRead,
	                               .dstAccessMask = vk::AccessFlagBits::eTransferRead,
	                               .oldLayout = vk::ImageLayout::eShaderReadOnlyOptimal,
	                               .newLayout = vk::ImageLayout::eTransferSrcOptimal,
	                               .srcQueueFamilyIndex = vk::QueueFamilyIgnored,
	                               .dstQueueFamilyIndex = vk::QueueFamilyIgnored,
	                               .image = image,
	                               .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}};
	cmd.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eTransfer, {}, {}, {}, barrier);
	cmd.copyImageToBuffer(image, vk::ImageLayout::eTransferSrcOptimal, buffer, vk::BufferImageCopy{.imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1}, .imageExtent = extent});
	vk::BufferMemoryBarrier host{.srcAccessMask = vk::AccessFlagBits::eTransferWrite, .dstAccessMask = vk::AccessFlagBits::eHostRead, .srcQueueFamilyIndex = vk::QueueFamilyIgnored, .dstQueueFamilyIndex = vk::QueueFamilyIgnored, .buffer = buffer, .size = vk::WholeSize};
	cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eHost, {}, {}, host, {});
	std::swap(barrier.srcAccessMask, barrier.dstAccessMask);
	std::swap(barrier.oldLayout, barrier.newLayout);
	cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eComputeShader, {}, {}, {}, barrier);
	pending = signal;
}
void hands_frame_capture::run()
{
	while (true)
	{
		bool ready = false;
		{
			std::unique_lock lock(mutex);
			condition.wait_for(lock, std::chrono::milliseconds(50), [&] { return stop || job; });
			if (stop && !job)
				return;
			ready = job;
			job = false;
		}
		requested = !directory.empty() && private_directory(directory) && lease(directory / "hands-frame.lease");
		if (!ready)
			continue;
        if (private_directory(directory))
        {
			std::array<uint8_t, 40> header{};
			std::memcpy(header.data(), "WVRHF001", 8);
			auto put = [&](unsigned at, uint64_t value, unsigned count) {for(unsigned i=0;i<count;i++)header[at+i]=uint8_t(value>>(i*8)); };
			put(8, width, 4);
			put(12, height, 4);
			put(16, timestamp, 8);
			for (auto [at, value]: {std::pair{24u, fov.angle_left}, {28u, fov.angle_right}, {32u, fov.angle_up}, {36u, fov.angle_down}})
				put(at, std::bit_cast<uint32_t>(value), 4);
            publish_hands_frame(directory / "hands-frame.rgba",
                std::as_bytes(std::span(header)),
                std::span(static_cast<const std::byte *>(buffer.map()), size_t(width) * height * 4));
        }
		busy = false;
	}
}
} // namespace wivrn
