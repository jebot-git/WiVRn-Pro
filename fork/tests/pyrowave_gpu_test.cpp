// SPDX-License-Identifier: GPL-3.0-or-later
// Hardware integration test: encode, packetize, fragmented decode and readback.
#include "pyrowave/pyrowave_encoder.h"
#include "pyrowave/pyrowave_decoder.h"
#include "vk/vk_allocator.h"
#include <cmath>
#include <iostream>
#include <stdexcept>

int main(int argc, char **)
try
{
    vk::raii::Context context;
    vk::ApplicationInfo app{.pApplicationName = "WiVRn PyroWave test", .apiVersion = VK_API_VERSION_1_3};
    vk::raii::Instance instance(context, {.pApplicationInfo = &app});
    auto devices = instance.enumeratePhysicalDevices();
    auto & physical = devices.at(0);
    std::cout << "GPU: " << physical.getProperties().deviceName << std::endl;
    auto features = physical.getFeatures2<vk::PhysicalDeviceFeatures2, vk::PhysicalDeviceVulkan11Features,
        vk::PhysicalDeviceVulkan12Features, vk::PhysicalDeviceVulkan13Features>();
    auto properties = physical.getQueueFamilyProperties();
    uint32_t family = 0;
    while (!(properties.at(family).queueFlags & vk::QueueFlagBits::eCompute)) ++family;
    float priority = 1;
    vk::DeviceQueueCreateInfo qinfo{.queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    vk::raii::Device device(physical, {.pNext = &features.get(), .queueCreateInfoCount = 1, .pQueueCreateInfos = &qinfo});
    auto queue = device.getQueue(family, 0);
    vk_allocator allocator({.physicalDevice = *physical, .device = *device, .instance = *instance,
                            .vulkanApiVersion = VK_API_VERSION_1_3}, false);
    PyroWave::Encoder encoder(physical, device, 128, 128, PyroWave::ChromaSubsampling::Chroma420);
    PyroWave::Decoder decoder(physical, device, 128, 128, PyroWave::ChromaSubsampling::Chroma420, false);
    auto pool = device.createCommandPool({.flags = vk::CommandPoolCreateFlagBits::eResetCommandBuffer, .queueFamilyIndex = family});
    auto command = std::move(device.allocateCommandBuffers({.commandPool = *pool, .commandBufferCount = 1})[0]);
    auto fence = device.createFence({});
    auto submit = [&] {
        command.end();
        queue.submit(vk::SubmitInfo{.commandBufferCount = 1, .pCommandBuffers = &*command}, *fence);
        if (device.waitForFences(*fence, true, 10'000'000'000) != vk::Result::eSuccess) throw std::runtime_error("GPU timeout");
        device.resetFences(*fence); command.reset();
    };
    auto host_buffer = [&](size_t size) { return buffer_allocation(device, {.size = size,
        .usage = vk::BufferUsageFlagBits::eStorageBuffer | vk::BufferUsageFlagBits::eTransferDst | vk::BufferUsageFlagBits::eTransferSrc},
        {.flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT, .usage = VMA_MEMORY_USAGE_AUTO,
         .requiredFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT}, "test host buffer"); };
    auto upload = host_buffer(128 * 128 * 3);
    std::vector<uint8_t> expected(128 * 128 * 3);
    for (unsigned plane = 0; plane < 3; ++plane) {
        unsigned size = plane ? 64 : 128;
        for (unsigned y = 0; y < size; ++y)
            for (unsigned x = 0; x < size; ++x)
                expected[plane * 128 * 128 + y * size + x] = argc > 1 ? 32 + (plane * 37 + x * 3 + y * 5) % 192
                    : 32 + plane * 24 + (x * 80 + y * 64) / size;
    }
    std::memcpy(upload.map(), expected.data(), expected.size());
    vmaFlushAllocation(allocator, upload, 0, VK_WHOLE_SIZE);
    std::array<image_allocation, 3> input, output;
    std::array<vk::raii::ImageView, 3> in_view{nullptr, nullptr, nullptr}, out_view{nullptr, nullptr, nullptr};
    std::array<vk::ImageView, 3> in{}, out{};
    command.begin({});
    for (unsigned plane = 0; plane < 3; ++plane)
    {
        uint32_t size = plane ? 64 : 128;
        for (bool dest : {false, true})
        {
            auto & image = dest ? output[plane] : input[plane];
            image = image_allocation(device, {.imageType = vk::ImageType::e2D, .format = vk::Format::eR8Unorm,
                .extent = {size, size, 1}, .mipLevels = 1, .arrayLayers = 1,
                .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eTransferSrc | vk::ImageUsageFlagBits::eTransferDst},
                {.usage = VMA_MEMORY_USAGE_AUTO}, "test plane");
            auto & view = dest ? out_view[plane] : in_view[plane];
            view = device.createImageView({.image = image, .viewType = vk::ImageViewType::e2D, .format = vk::Format::eR8Unorm,
                .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}});
            (dest ? out[plane] : in[plane]) = *view;
            vk::ImageMemoryBarrier barrier{.dstAccessMask = vk::AccessFlagBits::eTransferWrite | vk::AccessFlagBits::eShaderWrite,
                .oldLayout = vk::ImageLayout::eUndefined, .newLayout = vk::ImageLayout::eGeneral,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .image = image, .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}};
            command.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe, vk::PipelineStageFlagBits::eAllCommands, {}, {}, {}, barrier);
            if (!dest) command.copyBufferToImage(upload, image, vk::ImageLayout::eGeneral,
                vk::BufferImageCopy{.bufferOffset = plane * 128 * 128,
                    .imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1}, .imageExtent = {size, size, 1}});
        }
    }
    vk::MemoryBarrier ready{.srcAccessMask = vk::AccessFlagBits::eTransferWrite, .dstAccessMask = vk::AccessFlagBits::eShaderRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eComputeShader, {}, ready, {}, {});
    auto meta = host_buffer(encoder.get_meta_required_size());
    auto data = host_buffer(2 * 1024 * 1024);
    PyroWave::Encoder::BitstreamBuffers buffers{.meta = {.buffer = meta, .size = meta.info().size},
        .bitstream = {.buffer = data, .size = data.info().size}, .target_size = 65536};
    if (!encoder.encode(command, in, buffers)) throw std::runtime_error("encode failed");
    vk::MemoryBarrier host{.srcAccessMask = vk::AccessFlagBits::eShaderWrite, .dstAccessMask = vk::AccessFlagBits::eHostRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eHost, {}, host, {}, {});
    submit();
    vmaInvalidateAllocation(allocator, meta, 0, VK_WHOLE_SIZE); vmaInvalidateAllocation(allocator, data, 0, VK_WHOLE_SIZE);
    std::vector<uint8_t> wire(data.info().size + meta.info().size);
    std::vector<PyroWave::Encoder::Packet> packets(encoder.compute_num_packets(meta.map(), 8192));
    encoder.packetize(packets.data(), 8192, wire.data(), wire.size(), meta.map(), data.map());
    PyroWave::DecoderInput decoded(decoder);
    size_t bytes = 0;
    for (auto packet : packets)
    {
        bytes += packet.size;
        auto remaining = std::span<const uint8_t>(wire).subspan(packet.offset, packet.size);
        while (!remaining.empty()) {
            auto size = std::min<size_t>(remaining.size(), 137);
            if (!decoded.push_data(remaining.first(size))) throw std::runtime_error("packet decode failed");
            remaining = remaining.subspan(size);
        }
    }
    auto readback = host_buffer(128 * 128 * 3);
    command.begin({});
    if (!decoder.decode(command, decoded, out)) throw std::runtime_error("decode failed");
    vk::MemoryBarrier copy{.srcAccessMask = vk::AccessFlagBits::eShaderWrite, .dstAccessMask = vk::AccessFlagBits::eTransferRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eTransfer, {}, copy, {}, {});
    for (unsigned plane = 0; plane < 3; ++plane) {
        uint32_t size = plane ? 64 : 128;
        command.copyImageToBuffer(output[plane], vk::ImageLayout::eGeneral, readback,
            vk::BufferImageCopy{.bufferOffset = plane * 128 * 128,
                .imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1}, .imageExtent = {size, size, 1}});
    }
    host.srcAccessMask = vk::AccessFlagBits::eTransferWrite;
    command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eHost, {}, host, {}, {});
    submit();
    vmaInvalidateAllocation(allocator, readback, 0, VK_WHOLE_SIZE);
    const auto * pixels = readback.data<uint8_t>();
    double total_error = 0;
    unsigned max_error = 0, samples = 0;
    for (unsigned plane = 0; plane < 3; ++plane) {
        unsigned size = plane ? 64 : 128;
        for (unsigned i = 0; i < size * size; ++i) {
            unsigned error = std::abs(int(pixels[plane * 128 * 128 + i]) - expected[plane * 128 * 128 + i]);
            if (error > max_error) std::cout << "Largest error at plane " << plane << " x " << i % size << " y " << i / size
                << ": expected " << int(expected[plane * 128 * 128 + i]) << ", decoded " << int(pixels[plane * 128 * 128 + i]) << std::endl;
            max_error = std::max(max_error, error); total_error += error; ++samples;
        }
    }
    std::cout << "PyroWave GPU round trip: " << bytes << " encoded bytes, mean absolute error "
              << total_error / samples << ", max error " << max_error << std::endl;
    if (total_error / samples > 3 || max_error > 16) throw std::runtime_error("round-trip image error exceeds tolerance");

}
catch (const std::exception & e) { std::cerr << e.what() << std::endl; return 1; }
