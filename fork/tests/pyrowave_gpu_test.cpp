// SPDX-License-Identifier: GPL-3.0-or-later
// Hardware integration test: encode, packetize, fragmented decode and readback.
#include "pyrowave/pyrowave_encoder.h"
#include "pyrowave/pyrowave_decoder.h"
#include "vk/vk_allocator.h"
#include <cmath>
#include <iostream>
#include <stdexcept>
#include <fstream>
#include <chrono>
#include "pyrowave/color_conversion.h"

int main(int argc, char ** argv)
try
{
    uint32_t width = 128, height = 128, iterations = 30;
    bool stress = false, native_fp16 = false;
    std::string replay, dump;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--decode") replay = argv[++i];
        else if (arg == "--wire") dump = argv[++i];
        else if (arg == "--width") width = std::stoul(argv[++i]);
        else if (arg == "--height") height = std::stoul(argv[++i]);
        else if (arg == "--iterations") iterations = std::stoul(argv[++i]);
        else if (arg == "--stress") stress = true;
        else if (arg == "--fp16") native_fp16 = true;
        else throw std::runtime_error("unknown argument: " + arg);
    }
    std::vector<uint8_t> wire;
    if (!replay.empty()) {
        std::ifstream file(replay, std::ios::binary);
        uint32_t header[4]{}; file.read(reinterpret_cast<char *>(header), sizeof(header));
        if (header[0] != 0x51505731 || !file) throw std::runtime_error("invalid replay header");
        width = header[1]; height = header[2]; stress = header[3];
        wire.assign(std::istreambuf_iterator<char>(file), {});
    }
    if (!width || !height || width > 8192 || height > 8192 || (width % 2) || (height % 2) || !iterations || iterations > 10000)
        throw std::runtime_error("invalid test dimensions or iteration count");
    std::cout << "Frame " << width << "x" << height << " 4:4:4 Haar, iterations " << iterations << std::endl;
    vk::raii::Context context;
#ifdef __ANDROID__
    constexpr uint32_t api = VK_API_VERSION_1_1;
#else
    constexpr uint32_t api = VK_API_VERSION_1_3;
#endif
    vk::ApplicationInfo app{.pApplicationName = "WiVRn PyroWave test", .apiVersion = api};
    vk::raii::Instance instance(context, {.pApplicationInfo = &app});
    auto devices = instance.enumeratePhysicalDevices();
    auto & physical = devices.at(0);
    std::cout << "GPU: " << physical.getProperties().deviceName << std::endl;
#ifdef __ANDROID__
    auto features = physical.getFeatures2<vk::PhysicalDeviceFeatures2, vk::PhysicalDevice16BitStorageFeatures,
        vk::PhysicalDeviceSubgroupSizeControlFeaturesEXT, vk::PhysicalDeviceShaderFloat16Int8Features>();
    std::array<const char *, 3> extensions{VK_KHR_16BIT_STORAGE_EXTENSION_NAME, VK_EXT_SUBGROUP_SIZE_CONTROL_EXTENSION_NAME, VK_KHR_SHADER_FLOAT16_INT8_EXTENSION_NAME};
#else
    auto features = physical.getFeatures2<vk::PhysicalDeviceFeatures2, vk::PhysicalDeviceVulkan11Features,
        vk::PhysicalDeviceVulkan12Features, vk::PhysicalDeviceVulkan13Features>();
#endif
    auto properties = physical.getQueueFamilyProperties();
    uint32_t family = 0;
    while (!(properties.at(family).queueFlags & vk::QueueFlagBits::eCompute)) ++family;
    float priority = 1;
    vk::DeviceQueueCreateInfo qinfo{.queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    vk::DeviceCreateInfo device_info{.pNext = &features.get(), .queueCreateInfoCount = 1, .pQueueCreateInfos = &qinfo};
#ifdef __ANDROID__
    device_info.enabledExtensionCount = extensions.size(); device_info.ppEnabledExtensionNames = extensions.data();
#endif
    vk::raii::Device device(physical, device_info);
    auto queue = device.getQueue(family, 0);
    vk_allocator allocator({.physicalDevice = *physical, .device = *device, .instance = *instance,
                            .vulkanApiVersion = api}, false);
#ifndef __ANDROID__
    std::unique_ptr<PyroWave::Encoder> encoder;
    if (replay.empty()) encoder = std::make_unique<PyroWave::Encoder>(physical, device, width, height, PyroWave::ChromaSubsampling::Chroma444, true);
#else
    if (replay.empty()) throw std::runtime_error("Android test requires host-generated --decode file");
#endif
    PyroWave::Decoder decoder(physical, device, width, height, PyroWave::ChromaSubsampling::Chroma444, false, true, native_fp16);
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
    auto upload = host_buffer(size_t(width) * height * 3);
    std::vector<uint8_t> expected(size_t(width) * height * 3);
    for (unsigned plane = 0; plane < 3; ++plane) {
        unsigned size = width;
        for (unsigned y = 0; y < height; ++y)
            for (unsigned x = 0; x < size; ++x)
                expected[plane * size_t(width) * height + y * size + x] = stress ? 32 + (plane * 37 + x * 3 + y * 5) % 192
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
        uint32_t size = width;
        for (bool dest : {false, true})
        {
            auto & image = dest ? output[plane] : input[plane];
            image = image_allocation(device, {.imageType = vk::ImageType::e2D, .format = dest ? vk::Format::eR8G8B8A8Unorm : vk::Format::eR8Unorm,
                .extent = {dest ? (width + 1) / 2 : width, dest ? (height + 1) / 2 : height, 1}, .mipLevels = 1, .arrayLayers = 1,
                .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eTransferSrc | vk::ImageUsageFlagBits::eTransferDst},
                {.usage = VMA_MEMORY_USAGE_AUTO}, "test plane");
            auto & view = dest ? out_view[plane] : in_view[plane];
            view = device.createImageView({.image = image, .viewType = vk::ImageViewType::e2D, .format = dest ? vk::Format::eR8G8B8A8Unorm : vk::Format::eR8Unorm,
                .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}});
            (dest ? out[plane] : in[plane]) = *view;
            vk::ImageMemoryBarrier barrier{.dstAccessMask = vk::AccessFlagBits::eTransferWrite | vk::AccessFlagBits::eShaderWrite,
                .oldLayout = vk::ImageLayout::eUndefined, .newLayout = vk::ImageLayout::eGeneral,
                .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
                .image = image, .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}};
            command.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe, vk::PipelineStageFlagBits::eAllCommands, {}, {}, {}, barrier);
            if (!dest) command.copyBufferToImage(upload, image, vk::ImageLayout::eGeneral,
                vk::BufferImageCopy{.bufferOffset = plane * size_t(width) * height,
                    .imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1}, .imageExtent = {width, height, 1}});
        }
    }
    vk::MemoryBarrier ready{.srcAccessMask = vk::AccessFlagBits::eTransferWrite, .dstAccessMask = vk::AccessFlagBits::eShaderRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eComputeShader, {}, ready, {}, {});
    submit();
#ifndef __ANDROID__
    if (encoder) {
    command.begin({});
    auto meta = host_buffer(encoder->get_meta_required_size());
    auto data = host_buffer(std::max<size_t>(2 * 1024 * 1024, size_t(width) * height * 6));
    PyroWave::Encoder::BitstreamBuffers buffers{.meta = {.buffer = meta, .size = meta.info().size},
        .bitstream = {.buffer = data, .size = data.info().size}, .target_size = uint32_t(std::max<size_t>(1024 * 1024, size_t(width) * height * 3))};
    if (!encoder->encode(command, in, buffers)) throw std::runtime_error("encode failed");
    vk::MemoryBarrier host{.srcAccessMask = vk::AccessFlagBits::eShaderWrite, .dstAccessMask = vk::AccessFlagBits::eHostRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eHost, {}, host, {}, {});
    submit();
    vmaInvalidateAllocation(allocator, meta, 0, VK_WHOLE_SIZE); vmaInvalidateAllocation(allocator, data, 0, VK_WHOLE_SIZE);
    wire.resize(data.info().size + meta.info().size);
    std::vector<PyroWave::Encoder::Packet> packets(encoder->compute_num_packets(meta.map(), 8192));
    encoder->packetize(packets.data(), 8192, wire.data(), wire.size(), meta.map(), data.map());
    size_t wire_size = 0;
    for (auto packet : packets) wire_size = std::max(wire_size, size_t(packet.offset + packet.size));
    wire.resize(wire_size);
    if (!dump.empty()) {
        std::ofstream file(dump, std::ios::binary);
        uint32_t header[4]{0x51505731, width, height, uint32_t(stress)};
        file.write(reinterpret_cast<const char *>(header), sizeof(header));
        file.write(reinterpret_cast<const char *>(wire.data()), wire.size());
    }
    }
#endif
    PyroWave::DecoderInput decoded(decoder);
    size_t bytes = wire.size();
    auto remaining = std::span<const uint8_t>(wire);
    while (!remaining.empty()) {
        auto size = std::min<size_t>(remaining.size(), 137);
        if (!decoded.push_data(remaining.first(size))) throw std::runtime_error("fragment decode failed");
        remaining = remaining.subspan(size);
    }
    {
        PyroWave::DecoderInput truncated(decoder);
        if (!truncated.push_data(std::span<const uint8_t>(wire).first(wire.size() - 1)) || truncated.complete())
            throw std::runtime_error("truncated frame was accepted as complete");
        auto invalid_wire = wire;
        auto * header = reinterpret_cast<PyroWave::BitstreamSequenceHeader *>(invalid_wire.data());
        header->code = 3;
        PyroWave::DecoderInput invalid(decoder);
        if (invalid.push_data(invalid_wire)) throw std::runtime_error("unknown wavelet was accepted");
    }
    if (!decoded.complete()) throw std::runtime_error("incomplete frame accepted");
    vk::MemoryBarrier host{.srcAccessMask = vk::AccessFlagBits::eTransferWrite, .dstAccessMask = vk::AccessFlagBits::eHostRead};
    auto readback = host_buffer(size_t(width) * height * 3);
    command.begin({});
    if (!decoder.decode(command, decoded, out)) throw std::runtime_error("decode failed");
    vk::MemoryBarrier copy{.srcAccessMask = vk::AccessFlagBits::eShaderWrite, .dstAccessMask = vk::AccessFlagBits::eTransferRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eTransfer, {}, copy, {}, {});
    for (unsigned plane = 0; plane < 3; ++plane) {
        uint32_t size = width;
        command.copyImageToBuffer(output[plane], vk::ImageLayout::eGeneral, readback,
            vk::BufferImageCopy{.bufferOffset = plane * size_t(width) * height,
                .imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1}, .imageExtent = {(width + 1) / 2, (height + 1) / 2, 1}});
    }
    host.srcAccessMask = vk::AccessFlagBits::eTransferWrite;
    command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eHost, {}, host, {}, {});
    submit();
    auto queries = device.createQueryPool({.queryType = vk::QueryType::eTimestamp, .queryCount = 2});
    PyroWave::ColorConversion conversion(device, {width, height});
    image_allocation rgba(device, {.imageType = vk::ImageType::e2D, .format = vk::Format::eR8G8B8A8Unorm,
        .extent = {width, height, 1}, .mipLevels = 1, .arrayLayers = 1,
        .usage = vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eTransferSrc}, {.usage = VMA_MEMORY_USAGE_AUTO}, "test RGBA");
    auto rgba_view = device.createImageView({.image = rgba, .viewType = vk::ImageViewType::e2D, .format = vk::Format::eR8G8B8A8Unorm,
        .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}});
    std::vector<double> times;
    for (unsigned iteration = 0; iteration < iterations + 3; ++iteration) {
        command.begin({});
        command.resetQueryPool(*queries, 0, 2);
        vk::ImageMemoryBarrier rgba_barrier{.dstAccessMask = vk::AccessFlagBits::eShaderWrite,
            .oldLayout = vk::ImageLayout::eUndefined, .newLayout = vk::ImageLayout::eGeneral,
            .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = rgba,
            .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}};
        command.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe, vk::PipelineStageFlagBits::eComputeShader, {}, {}, {}, rgba_barrier);
        command.writeTimestamp(vk::PipelineStageFlagBits::eTopOfPipe, *queries, 0);
        auto planes = conversion.begin(command);
        if (!decoder.decode(command, decoded, planes)) throw std::runtime_error("repeated decode failed");
        conversion.convert(command, *rgba_view);
        command.writeTimestamp(vk::PipelineStageFlagBits::eBottomOfPipe, *queries, 1);
        submit();
        auto [query_result, timestamps] = queries.getResults<uint64_t>(0, 2, 2 * sizeof(uint64_t), sizeof(uint64_t), vk::QueryResultFlagBits::e64 | vk::QueryResultFlagBits::eWait);
        if (iteration >= 3) times.push_back((timestamps[1] - timestamps[0]) * physical.getProperties().limits.timestampPeriod / 1e6);
    }
    std::ranges::sort(times);
    std::cout << "GPU decode + 4:4:4 RGBA conversion ms: median " << times[times.size()/2]
              << " p95 " << times[std::min(times.size()-1, size_t(times.size()*0.95))] << " max " << times.back() << std::endl;
    auto rgba_readback = host_buffer(size_t(width) * height * 4);
    command.begin({});
    vk::MemoryBarrier rgba_copy{.srcAccessMask = vk::AccessFlagBits::eShaderWrite, .dstAccessMask = vk::AccessFlagBits::eTransferRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eTransfer, {}, rgba_copy, {}, {});
    command.copyImageToBuffer(rgba, vk::ImageLayout::eGeneral, rgba_readback,
        vk::BufferImageCopy{.imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1}, .imageExtent = {width, height, 1}});
    command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eHost, {}, host, {}, {});
    submit();
    vmaInvalidateAllocation(allocator, rgba_readback, 0, VK_WHOLE_SIZE);
    vmaInvalidateAllocation(allocator, readback, 0, VK_WHOLE_SIZE);
    const auto * pixels = readback.data<uint8_t>();
    auto packed_pixel = [&](unsigned plane, unsigned i) {
        return pixels[plane * size_t(width) * height + 4 * ((i / width / 2) * ((width + 1)/2) + (i % width / 2)) + ((i % width) & 1) + 2 * ((i / width) & 1)];
    };
    const auto * rgb = rgba_readback.data<uint8_t>();
    unsigned color_error = 0;
    for (unsigned i = 0; i < width * height; ++i) {
        float y = packed_pixel(0, i) / 255.f;
        float cb = packed_pixel(1, i) / 255.f - 0.5f;
        float cr = packed_pixel(2, i) / 255.f - 0.5f;
        std::array<float, 3> values{y + 1.5748f * cr, y - 0.187324f * cb - 0.468124f * cr, y + 1.8556f * cb};
        for (unsigned c = 0; c < 3; ++c)
            color_error = std::max(color_error, unsigned(std::abs(int(rgb[4*i+c]) - int(std::lround(std::clamp(values[c], 0.f, 1.f) * 255)))));
        if (rgb[4*i+3] != 255) throw std::runtime_error("invalid RGBA alpha");
    }
    std::cout << "GPU BT.709 conversion max error " << color_error << std::endl;
    if (color_error > 1) throw std::runtime_error("GPU color conversion differs from CPU reference");
    double total_error = 0;
    unsigned max_error = 0, samples = 0;
    for (unsigned plane = 0; plane < 3; ++plane) {
        unsigned size = width;
        for (unsigned i = 0; i < size * height; ++i) {
            unsigned error = std::abs(int(pixels[plane * size_t(width) * height + 4 * ((i / width / 2) * ((width + 1)/2) + (i % width / 2)) + ((i % width) & 1) + 2 * ((i / width) & 1)]) - expected[plane * size_t(width) * height + i]);
            if (error > max_error && error > 16) std::cout << "Largest error at plane " << plane << " x " << i % size << " y " << i / size
                << ": expected " << int(expected[plane * size_t(width) * height + i]) << ", decoded " << int(pixels[plane * size_t(width) * height + 4 * ((i / width / 2) * ((width + 1)/2) + (i % width / 2)) + ((i % width) & 1) + 2 * ((i / width) & 1)]) << std::endl;
            max_error = std::max(max_error, error); total_error += error; ++samples;
        }
    }
    std::cout << "PyroWave GPU round trip: " << bytes << " encoded bytes, mean absolute error "
              << total_error / samples << ", max error " << max_error << std::endl;
    if (total_error / samples > 3 || max_error > 16) throw std::runtime_error("round-trip image error exceeds tolerance");

}
catch (const std::exception & e) { std::cerr << e.what() << std::endl; return 1; }
