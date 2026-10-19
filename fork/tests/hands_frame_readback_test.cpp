// SPDX-License-Identifier: GPL-3.0-or-later
#include "compositor/hands_frame_capture.h"
#include "os/os_time.h"
#include <cassert>
#ifdef NDEBUG
#error Readback tests require assertions
#endif
#include <fstream>
#include <sys/stat.h>
#include <unistd.h>
int main()
{
	char runtime[] = "/tmp/wivrn-hands-readback-XXXXXX";
	auto created = mkdtemp(runtime);
	assert(created);
	if (!created)
		return 1;
	setenv("XDG_RUNTIME_DIR", runtime, 1);
	auto directory = std::filesystem::path(runtime) / "wivrn-pro";
	std::filesystem::create_directory(directory);
	chmod(directory.c_str(), 0700);
	uint64_t stamp = os_monotonic_get_ns();
	{
		std::ofstream file(directory / "hands-frame.lease", std::ios::binary);
		file.write(reinterpret_cast<char *>(&stamp), 8);
	}
	chmod((directory / "hands-frame.lease").c_str(), 0600);
	vk::raii::Context context;
	vk::ApplicationInfo application{.pApplicationName = "Hands Frame offline readback test", .apiVersion = VK_API_VERSION_1_2};
	vk::raii::Instance instance(context, vk::InstanceCreateInfo{.pApplicationInfo = &application});
	auto physical = instance.enumeratePhysicalDevices().front();
	auto families = physical.getQueueFamilyProperties();
	uint32_t family = 0;
	for (uint32_t i = 0; i < families.size(); i++)
		if (families[i].queueFlags & vk::QueueFlagBits::eCompute)
		{
			family = i;
			break;
		}
	float priority = 1;
	vk::DeviceQueueCreateInfo queue_info{.queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
	vk::PhysicalDeviceTimelineSemaphoreFeatures timeline{.timelineSemaphore = true};
	vk::raii::Device device(physical, vk::DeviceCreateInfo{.pNext = &timeline, .queueCreateInfoCount = 1, .pQueueCreateInfos = &queue_info});
	vk::raii::Queue queue(device, family, 0);
	vk_allocator allocator(VmaAllocatorCreateInfo{.physicalDevice = *physical, .device = *device, .instance = *instance, .vulkanApiVersion = VK_API_VERSION_1_2}, false);
	{
		wivrn::hands_frame_capture capture;
		for (int i = 0; i < 50 && !capture.enabled(); i++)
			usleep(10'000);
		assert(capture.enabled());
		image_allocation image(device, {.imageType = vk::ImageType::e2D, .format = vk::Format::eR8G8B8A8Srgb, .extent = {16, 16, 1}, .mipLevels = 1, .arrayLayers = 2, .samples = vk::SampleCountFlagBits::e1, .tiling = vk::ImageTiling::eOptimal, .usage = vk::ImageUsageFlagBits::eTransferSrc | vk::ImageUsageFlagBits::eTransferDst | vk::ImageUsageFlagBits::eSampled, .sharingMode = vk::SharingMode::eExclusive}, {.usage = VMA_MEMORY_USAGE_AUTO});
		vk::raii::CommandPool pool(device, vk::CommandPoolCreateInfo{.queueFamilyIndex = family});
		vk::raii::CommandBuffers commands(device, vk::CommandBufferAllocateInfo{.commandPool = *pool, .level = vk::CommandBufferLevel::ePrimary, .commandBufferCount = 1});
		auto & cmd = commands.front();
		cmd.begin(vk::CommandBufferBeginInfo{});
		vk::ImageMemoryBarrier barrier{.dstAccessMask = vk::AccessFlagBits::eTransferWrite, .oldLayout = vk::ImageLayout::eUndefined, .newLayout = vk::ImageLayout::eTransferDstOptimal, .srcQueueFamilyIndex = vk::QueueFamilyIgnored, .dstQueueFamilyIndex = vk::QueueFamilyIgnored, .image = image, .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 2}};
		cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe, vk::PipelineStageFlagBits::eTransfer, {}, {}, {}, barrier);
		cmd.clearColorImage(image, vk::ImageLayout::eTransferDstOptimal, vk::ClearColorValue{std::array<float, 4>{1, 0, 0, 1}}, barrier.subresourceRange);
		barrier.srcAccessMask = vk::AccessFlagBits::eTransferWrite;
		barrier.dstAccessMask = vk::AccessFlagBits::eShaderRead;
		barrier.oldLayout = vk::ImageLayout::eTransferDstOptimal;
		barrier.newLayout = vk::ImageLayout::eShaderReadOnlyOptimal;
		cmd.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eComputeShader, {}, {}, {}, barrier);
		vk::SemaphoreTypeCreateInfo type{.semaphoreType = vk::SemaphoreType::eTimeline};
		vk::raii::Semaphore semaphore(device, vk::SemaphoreCreateInfo{.pNext = &type});
		capture.record(device, *cmd, image, {16, 16, 1}, xrt_fov{-.7f, .7f, .7f, -.7f}, 1);
		cmd.end();
		uint64_t value = 1;
		vk::TimelineSemaphoreSubmitInfo signal{.signalSemaphoreValueCount = 1, .pSignalSemaphoreValues = &value};
		vk::CommandBuffer raw = *cmd;
		vk::Semaphore sem = *semaphore;
		queue.submit(vk::SubmitInfo{.pNext = &signal, .commandBufferCount = 1, .pCommandBuffers = &raw, .signalSemaphoreCount = 1, .pSignalSemaphores = &sem});
		auto path = directory / "hands-frame.rgba";
		for (int i = 0; i < 100; i++)
		{
			capture.poll(device, semaphore);
			if (std::filesystem::exists(path) && std::filesystem::file_size(path) == 40 + 16 * 16 * 4)
				break;
			usleep(5'000);
		}
		std::ifstream file(path, std::ios::binary);
		std::vector<unsigned char> bytes((std::istreambuf_iterator<char>(file)), {});
		assert(bytes.size() == 40 + 16 * 16 * 4);
		assert(std::memcmp(bytes.data(), "WVRHF001", 8) == 0);
		for (size_t i = 40; i < bytes.size(); i += 4)
		{
			assert(bytes[i] == 255 && bytes[i + 1] == 0 && bytes[i + 2] == 0 && bytes[i + 3] == 255);
		}
		// Stopping the lease shuts readback down without another request.
		std::filesystem::remove(directory / "hands-frame.lease");
		for (int i = 0; i < 50 && capture.enabled(); i++)
			usleep(5'000);
		assert(!capture.enabled());
		device.waitIdle();
	}
	assert(!std::filesystem::exists(directory / "hands-frame.rgba"));
	std::filesystem::remove_all(runtime);
}
