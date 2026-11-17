// SPDX-License-Identifier: GPL-3.0-or-later
// Explicit hardware test of the production foveation shader's Packed444 branch.
// Compile server/compositor/shaders/foveation.comp to SPIR-V and pass its path.
#include "packed444/layout.h"
#include "compositor/foveation_mapping.h"
#include "vk/allocation.h"
#include "vk/vk_allocator.h"
#include <algorithm>
#include <array>
#include <cmath>
#include <cstring>
#include <fstream>
#include <iostream>
#include <span>
#include <stdexcept>
#include <vector>

namespace
{
constexpr unsigned max_dim = 4097;
struct Mapping { std::array<uint32_t, 2 * max_dim> x{}, y{}; };
using Pixel = std::array<float, 4>;

void require(bool value, const char * message)
{
    if (!value) throw std::runtime_error(message);
}

std::vector<uint32_t> coordinates(unsigned encoded, unsigned source, float center, bool flip)
{
    std::vector<uint16_t> spans;
    if (encoded == source) spans = {uint16_t(source)};
    else wivrn::foveation_math::fill_param_2d(center, encoded, source, spans);
    std::vector<uint32_t> out{flip ? source : 0};
    for (unsigned i = 0; i < spans.size(); ++i) {
        unsigned step = unsigned(std::abs(int(spans.size() / 2) - int(i))) + 1;
        for (unsigned n = 0; n < spans[i]; ++n)
            out.push_back(flip ? out.back() - step : out.back() + step);
    }
    require(out.size() == encoded + 1, "Foveation coordinate count mismatch");
    require(out.back() == (flip ? 0 : source), "Foveation source coverage mismatch");
    return out;
}

float srgb(float v)
{
    return v < 0.0031308f ? 12.92f * v : 1.055f * std::pow(v, 1.f / 2.4f) - 0.055f;
}

uint8_t unorm(float value)
{
    return uint8_t(std::lround(std::clamp(value, 0.f, 1.f) * 255));
}

// Mode 0 is legacy420, mode1 is PyroWave444, mode2 is the new packed layout.
struct TestCase { const char * name; unsigned source_width, source_height; bool foveated, flip, alpha; unsigned mode = 2; };

void run_case(vk::raii::Device & device, vk::raii::Queue & queue,
              vk::raii::ShaderModule & shader, unsigned family, const TestCase & test)
{
    constexpr unsigned width = 32, height = 24;
    auto packed_size = wivrn::packed444::coded_extent(width, height);
    require(wivrn::packed444::logical_extent(packed_size.width, packed_size.height) ==
                wivrn::packed444::extent{width, height}, "Packed geometry mismatch");
    auto coded = test.mode == 2 ? packed_size : wivrn::packed444::extent{width, height};
    const unsigned luma_components = test.mode == 1 ? 4 : 1;
    const unsigned chroma_width = coded.width / 2;
    auto host_buffer = [&](size_t size, const char * name) {
        return buffer_allocation(device, {.size = size,
            .usage = vk::BufferUsageFlagBits::eTransferSrc | vk::BufferUsageFlagBits::eTransferDst | vk::BufferUsageFlagBits::eStorageBuffer},
            {.flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT, .usage = VMA_MEMORY_USAGE_AUTO,
             .requiredFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT}, name);
    };
    std::array<std::vector<Pixel>, 2> pixels;
    for (unsigned eye = 0; eye < 2; ++eye) {
        pixels[eye].resize(test.source_width * test.source_height);
        for (unsigned y = 0; y < test.source_height; ++y)
            for (unsigned x = 0; x < test.source_width; ++x)
                pixels[eye][y * test.source_width + x] = {
                    ((x + eye) & 1) ? .9f : .05f,
                    ((y + eye) & 1) ? .7f : .15f,
                    ((x ^ y ^ eye) & 1) ? .8f : .1f,
                    float((37 * x + 53 * y + 71 * eye) % 256) / 255};
    }
    Mapping mapping;
    for (unsigned eye = 0; eye < 2; ++eye) {
        auto xs = coordinates(width, test.source_width, test.foveated ? (eye ? -.35f : .25f) : 0, false);
        auto ys = coordinates(height, test.source_height, test.foveated ? (eye ? .4f : -.2f) : 0, test.flip);
        std::copy(xs.begin(), xs.end(), mapping.x.begin() + eye * max_dim);
        std::copy(ys.begin(), ys.end(), mapping.y.begin() + eye * max_dim);
    }
    auto config = host_buffer(sizeof(mapping), "Packed444 test mapping");
    std::memcpy(config.map(), &mapping, sizeof(mapping));
    vmaFlushAllocation(vk_allocator::instance(), config, 0, VK_WHOLE_SIZE);
    size_t eye_bytes = pixels[0].size() * sizeof(Pixel);
    auto upload = host_buffer(eye_bytes * 2, "Packed444 test source");
    for (unsigned eye = 0; eye < 2; ++eye)
        std::memcpy(static_cast<char *>(upload.map()) + eye * eye_bytes, pixels[eye].data(), eye_bytes);
    vmaFlushAllocation(vk_allocator::instance(), upload, 0, VK_WHOLE_SIZE);
    std::array<image_allocation, 2> source;
    std::array<vk::raii::ImageView, 2> source_views{nullptr, nullptr};
    for (unsigned eye = 0; eye < 2; ++eye) {
        source[eye] = image_allocation(device, {.imageType = vk::ImageType::e2D,
            .format = vk::Format::eR32G32B32A32Sfloat, .extent = {test.source_width, test.source_height, 1},
            .mipLevels = 1, .arrayLayers = 1,
            .usage = vk::ImageUsageFlagBits::eSampled | vk::ImageUsageFlagBits::eTransferDst},
            {.usage = VMA_MEMORY_USAGE_AUTO}, "Packed444 synthetic eye");
        source_views[eye] = device.createImageView({.image = source[eye], .viewType = vk::ImageViewType::e2D,
            .format = vk::Format::eR32G32B32A32Sfloat,
            .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}});
    }
    std::array<image_allocation, 2> output;
    std::array<vk::raii::ImageView, 2> output_views{nullptr, nullptr};
    std::array<vk::Format, 2> formats{test.mode == 1 ? vk::Format::eR8G8B8A8Unorm : vk::Format::eR8Unorm, vk::Format::eR8G8Unorm};
    std::array<vk::Extent3D, 2> sizes{{{coded.width, height, 1}, {chroma_width, height / 2, 1}}};
    for (unsigned plane = 0; plane < 2; ++plane) {
        output[plane] = image_allocation(device, {.imageType = vk::ImageType::e2D, .format = formats[plane],
            .extent = sizes[plane], .mipLevels = 1, .arrayLayers = 3,
            .usage = vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eTransferSrc | vk::ImageUsageFlagBits::eTransferDst},
            {.usage = VMA_MEMORY_USAGE_AUTO}, "Packed444 output plane");
        output_views[plane] = device.createImageView({.image = output[plane], .viewType = vk::ImageViewType::e2DArray,
            .format = formats[plane],
            .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 3}});
    }
    auto sampler = device.createSampler({.magFilter = vk::Filter::eNearest, .minFilter = vk::Filter::eNearest,
        .addressModeU = vk::SamplerAddressMode::eClampToEdge, .addressModeV = vk::SamplerAddressMode::eClampToEdge,
        .addressModeW = vk::SamplerAddressMode::eClampToEdge});
    std::array<vk::DescriptorSetLayoutBinding, 4> bindings{{
        {.binding = 0, .descriptorType = vk::DescriptorType::eCombinedImageSampler, .descriptorCount = 2, .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 1, .descriptorType = vk::DescriptorType::eStorageBuffer, .descriptorCount = 1, .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 2, .descriptorType = vk::DescriptorType::eStorageImage, .descriptorCount = 1, .stageFlags = vk::ShaderStageFlagBits::eCompute},
        {.binding = 3, .descriptorType = vk::DescriptorType::eStorageImage, .descriptorCount = 1, .stageFlags = vk::ShaderStageFlagBits::eCompute}}};
    auto set_layout = device.createDescriptorSetLayout({.bindingCount = bindings.size(), .pBindings = bindings.data()});
    auto layout = device.createPipelineLayout({.setLayoutCount = 1, .pSetLayouts = &*set_layout});
    std::array<uint32_t, 3> constants{test.alpha ? width / 2 : 0, test.mode == 1, test.mode == 2};
    std::array<vk::SpecializationMapEntry, 3> entries{};
    for (unsigned i = 0; i < entries.size(); ++i) entries[i] = vk::SpecializationMapEntry{.constantID = i, .offset = uint32_t(i * sizeof(uint32_t)), .size = sizeof(uint32_t)};
    vk::SpecializationInfo specialization{.mapEntryCount = entries.size(), .pMapEntries = entries.data(), .dataSize = sizeof(constants), .pData = constants.data()};
    auto pipeline = device.createComputePipeline(nullptr, {.stage = {.flags = vk::PipelineShaderStageCreateFlagBits::eRequireFullSubgroups, .stage = vk::ShaderStageFlagBits::eCompute,
        .module = *shader, .pName = "main", .pSpecializationInfo = &specialization}, .layout = *layout});
    std::array<vk::DescriptorPoolSize, 3> pool_sizes{{
        {.type = vk::DescriptorType::eCombinedImageSampler, .descriptorCount = 2},
        {.type = vk::DescriptorType::eStorageBuffer, .descriptorCount = 1},
        {.type = vk::DescriptorType::eStorageImage, .descriptorCount = 2}}};
    auto descriptor_pool = device.createDescriptorPool({.flags = vk::DescriptorPoolCreateFlagBits::eFreeDescriptorSet,
        .maxSets = 1, .poolSizeCount = pool_sizes.size(), .pPoolSizes = pool_sizes.data()});
    auto sets = device.allocateDescriptorSets({.descriptorPool = *descriptor_pool, .descriptorSetCount = 1, .pSetLayouts = &*set_layout});
    std::array<vk::DescriptorImageInfo, 2> sources{{
        {.sampler = *sampler, .imageView = *source_views[0], .imageLayout = vk::ImageLayout::eGeneral},
        {.sampler = *sampler, .imageView = *source_views[1], .imageLayout = vk::ImageLayout::eGeneral}}};
    std::array<vk::DescriptorImageInfo, 2> outputs{{
        {.imageView = *output_views[0], .imageLayout = vk::ImageLayout::eGeneral},
        {.imageView = *output_views[1], .imageLayout = vk::ImageLayout::eGeneral}}};
    vk::DescriptorBufferInfo config_info{.buffer = config, .range = sizeof(mapping)};
    std::array<vk::WriteDescriptorSet, 4> writes{{
        {.dstSet = *sets[0], .dstBinding = 0, .descriptorCount = 2, .descriptorType = vk::DescriptorType::eCombinedImageSampler, .pImageInfo = sources.data()},
        {.dstSet = *sets[0], .dstBinding = 1, .descriptorCount = 1, .descriptorType = vk::DescriptorType::eStorageBuffer, .pBufferInfo = &config_info},
        {.dstSet = *sets[0], .dstBinding = 2, .descriptorCount = 1, .descriptorType = vk::DescriptorType::eStorageImage, .pImageInfo = &outputs[0]},
        {.dstSet = *sets[0], .dstBinding = 3, .descriptorCount = 1, .descriptorType = vk::DescriptorType::eStorageImage, .pImageInfo = &outputs[1]}}};
    device.updateDescriptorSets(writes, {});
    auto commands = device.createCommandPool({.queueFamilyIndex = family});
    auto command = std::move(device.allocateCommandBuffers({.commandPool = *commands, .commandBufferCount = 1})[0]);
    auto y_readback = host_buffer(size_t(coded.width) * height * 3 * luma_components, "Packed444 Y readback");
    auto uv_readback = host_buffer(size_t(chroma_width) * height / 2 * 3 * 2, "Packed444 UV readback");
    command.begin({.flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
    auto transition = [&](vk::Image image, unsigned layers) {
        vk::ImageMemoryBarrier b{.dstAccessMask = vk::AccessFlagBits::eTransferWrite, .oldLayout = vk::ImageLayout::eUndefined,
            .newLayout = vk::ImageLayout::eGeneral, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
            .image = image, .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = layers}};
        command.pipelineBarrier(vk::PipelineStageFlagBits::eTopOfPipe, vk::PipelineStageFlagBits::eTransfer, {}, {}, {}, b);
    };
    for (unsigned eye = 0; eye < 2; ++eye) {
        transition(source[eye], 1);
        command.copyBufferToImage(upload, source[eye], vk::ImageLayout::eGeneral,
            vk::BufferImageCopy{.bufferOffset = eye * eye_bytes,
                .imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 1},
                .imageExtent = {test.source_width, test.source_height, 1}});
    }
    for (unsigned plane = 0; plane < 2; ++plane) {
        transition(output[plane], 3);
        command.clearColorImage(output[plane], vk::ImageLayout::eGeneral, vk::ClearColorValue(std::array<float, 4>{.25f,.25f,.25f,.25f}),
            vk::ImageSubresourceRange{.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 3});
    }
    vk::MemoryBarrier ready{.srcAccessMask = vk::AccessFlagBits::eTransferWrite, .dstAccessMask = vk::AccessFlagBits::eShaderRead | vk::AccessFlagBits::eShaderWrite};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eComputeShader, {}, ready, {}, {});
    command.bindPipeline(vk::PipelineBindPoint::eCompute, *pipeline);
    command.bindDescriptorSets(vk::PipelineBindPoint::eCompute, *layout, 0, *sets[0], {});
    command.dispatch(width / 8, height / 8, 2);
    vk::MemoryBarrier copy{.srcAccessMask = vk::AccessFlagBits::eShaderWrite | vk::AccessFlagBits::eTransferWrite, .dstAccessMask = vk::AccessFlagBits::eTransferRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader | vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eTransfer, {}, copy, {}, {});
    command.copyImageToBuffer(output[0], vk::ImageLayout::eGeneral, y_readback,
        vk::BufferImageCopy{.imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 3}, .imageExtent = sizes[0]});
    command.copyImageToBuffer(output[1], vk::ImageLayout::eGeneral, uv_readback,
        vk::BufferImageCopy{.imageSubresource = {.aspectMask = vk::ImageAspectFlagBits::eColor, .layerCount = 3}, .imageExtent = sizes[1]});
    vk::MemoryBarrier host{.srcAccessMask = vk::AccessFlagBits::eTransferWrite, .dstAccessMask = vk::AccessFlagBits::eHostRead};
    command.pipelineBarrier(vk::PipelineStageFlagBits::eTransfer, vk::PipelineStageFlagBits::eHost, {}, host, {}, {});
    command.end();auto fence = device.createFence({});
    queue.submit(vk::SubmitInfo{.commandBufferCount = 1, .pCommandBuffers = &*command}, *fence);
    require(device.waitForFences(*fence, true, 10'000'000'000) == vk::Result::eSuccess, "Packed shader GPU timeout");
    vmaInvalidateAllocation(vk_allocator::instance(), y_readback, 0, VK_WHOLE_SIZE);
    vmaInvalidateAllocation(vk_allocator::instance(), uv_readback, 0, VK_WHOLE_SIZE);

    std::vector<uint8_t> expect_y(size_t(coded.width) * height * 3 * luma_components, unorm(.25f));
    std::vector<uint8_t> expect_uv(size_t(chroma_width) * height / 2 * 3 * 2, unorm(.25f));
    std::array<std::vector<Pixel>, 2> values;
    for (unsigned eye = 0; eye < 2; ++eye) {
        values[eye].resize(width * height);
        for (unsigned iy = 0; iy < height; ++iy) for (unsigned ix = 0; ix < width; ++ix) {
            unsigned xmin = mapping.x[eye * max_dim + ix], xmax = mapping.x[eye * max_dim + ix + 1];
            unsigned ya = mapping.y[eye * max_dim + iy], yb = mapping.y[eye * max_dim + iy + 1];
            unsigned ymin = std::min(ya,yb), ymax = std::max(ya,yb);
            unsigned xstep = (xmax - xmin + 3) / 4, ystep = (ymax - ymin + 3) / 4, count = 0;
            Pixel colour{};
            for (unsigned y = ymin; y < ymax; y += ystep) for (unsigned x = xmin; x < xmax; x += xstep) {
                auto pixel = pixels[eye][y * test.source_width + x];
                for (unsigned c = 0; c < 4; ++c) colour[c] += pixel[c];
                ++count;
            }
            for (auto & v : colour) v /= std::max(count,1u);
            float r = srgb(colour[0]), g = srgb(colour[1]), b = srgb(colour[2]);
            Pixel converted{.2126f*r + .7152f*g + .0722f*b,
                -.1146f*r - .3854f*g + .5f*b + .5f,
                .5f*r - .4542f*g - .0458f*b + .5f, colour[3]};
            values[eye][iy * width + ix] = converted;
            size_t y_where = ((eye * height + iy) * coded.width + ix) * luma_components;
            expect_y[y_where] = unorm(converted[0]);
            if (test.mode == 2) {
                expect_y[(eye * height + iy) * coded.width + width + ix] = unorm(converted[1]);
                expect_uv[((eye * (height / 2) + iy / 2) * chroma_width + ix) * 2 + (iy & 1)] = unorm(converted[2]);
            } else if (test.mode == 1) {
                expect_y[y_where + 1] = unorm(converted[1]);
                expect_y[y_where + 2] = unorm(converted[2]);
                expect_y[y_where + 3] = 255;
            }
        }
    }
    if (test.mode == 0) for (unsigned eye = 0; eye < 2; ++eye)
        for (unsigned iy = 0; iy < height; iy += 2) for (unsigned ix = 0; ix < width; ix += 2) {
            for (unsigned c = 1; c <= 2; ++c) {
                float mean = (values[eye][iy * width + ix][c] + values[eye][iy * width + ix + 1][c] +
                    values[eye][(iy + 1) * width + ix][c] + values[eye][(iy + 1) * width + ix + 1][c]) / 4;
                expect_uv[((eye * (height / 2) + iy / 2) * chroma_width + ix / 2) * 2 + c - 1] = unorm(mean);
            }
        }
    if (test.alpha) for (unsigned eye = 0; eye < 2; ++eye)
        for (unsigned iy = 0; iy < height; iy += 2) for (unsigned ix = 0; ix < width; ix += 2) {
            unsigned ax = eye * width / 2 + ix / 2;
            float alpha = (values[eye][iy * width + ix][3] + values[eye][iy * width + ix + 1][3] +
                values[eye][(iy + 1) * width + ix][3] + values[eye][(iy + 1) * width + ix + 1][3]) / 4;
            size_t where_y = ((2 * height + iy / 2) * coded.width + ax) * luma_components;
            expect_y[where_y] = unorm(alpha);
            if (test.mode == 2) expect_y[(2 * height + iy / 2) * coded.width + width + ax] = unorm(.5f);
            if (test.mode == 1) {
                expect_y[where_y + 1] = expect_y[where_y + 2] = unorm(.5f);
                expect_y[where_y + 3] = 255;
            }
            if (test.mode == 2 && iy % 4 == 0) {
                size_t where = ((2 * (height / 2) + iy / 4) * chroma_width + ax) * 2;
                expect_uv[where] = expect_uv[where + 1] = unorm(.5f);
            }
        }
    // The existing420 shader fills its allocated alpha UV plane with neutral
    // chroma, including rows below the encoder's H/4 active UV crop.
    if (test.mode == 0 && test.alpha)
        std::fill(expect_uv.begin() + size_t(chroma_width) * height / 2 * 2 * 2, expect_uv.end(), unorm(.5f));
    unsigned max_error = 0;
    auto compare = [&](std::span<const uint8_t> expect, const uint8_t * actual, const char * plane) {
        for (size_t i = 0; i < expect.size(); ++i) {
            unsigned error = unsigned(std::abs(int(expect[i]) - int(actual[i])));
            max_error = std::max(max_error,error);
            if (error > 1) {
                std::cerr << test.name << ' ' << plane << " byte " << i << " expected " << unsigned(expect[i]) << " got " << unsigned(actual[i]) << '\n';
                throw std::runtime_error("Packed444 GPU mapping differs from CPU reference");
            }
        }
    };
    compare(expect_y, y_readback.data<uint8_t>(), "Y");compare(expect_uv, uv_readback.data<uint8_t>(), "UV");
    std::cout << "PASS " << test.name << ": both eyes, mode " << test.mode << (test.alpha ? ", alpha" : ", untouched alpha layer") << ", max error " << max_error << "/255\n";
}
}

int main(int argc, char ** argv)
try {
    require(argc == 2, "Pass compiled production foveation SPIR-V as the sole argument");
    std::ifstream file(argv[1], std::ios::binary | std::ios::ate);require(bool(file), "Cannot open foveation SPIR-V");
    auto size = file.tellg();require(size > 0 && size % 4 == 0, "Invalid SPIR-V length");
    std::vector<uint32_t> spirv(size_t(size) / 4);file.seekg(0);file.read(reinterpret_cast<char *>(spirv.data()),size);require(bool(file), "Incomplete SPIR-V read");
    vk::raii::Context context;vk::ApplicationInfo app{.pApplicationName = "Packed444 foveation GPU test", .apiVersion = VK_API_VERSION_1_3};
    vk::raii::Instance instance(context,{.pApplicationInfo = &app});auto devices = instance.enumeratePhysicalDevices();require(!devices.empty(), "No Vulkan GPU");
    auto & physical = devices[0];auto features = physical.getFeatures();
    auto feature_chain = physical.getFeatures2<vk::PhysicalDeviceFeatures2, vk::PhysicalDeviceVulkan13Features>();
    auto & subgroup_features = feature_chain.get<vk::PhysicalDeviceVulkan13Features>();
    require(subgroup_features.subgroupSizeControl && subgroup_features.computeFullSubgroups, "Missing full subgroup support");
    require(features.shaderStorageImageWriteWithoutFormat && features.shaderSampledImageArrayDynamicIndexing, "Missing shader image features");
    auto props = physical.getProperties2<vk::PhysicalDeviceProperties2, vk::PhysicalDeviceSubgroupProperties>();
    auto subgroup_size = props.get<vk::PhysicalDeviceSubgroupProperties>().subgroupSize;
    require(subgroup_size >= 4 && 64 % subgroup_size == 0, "Default subgroup size must hold a quad and divide the flat 64-invocation group");
    auto required = vk::SubgroupFeatureFlagBits::eBasic | vk::SubgroupFeatureFlagBits::eShuffleRelative;
    require((props.get<vk::PhysicalDeviceSubgroupProperties>().supportedOperations & required) == required, "Missing subgroup shuffle support");
    auto queues = physical.getQueueFamilyProperties();unsigned family = 0;
    while (family < queues.size() && !(queues[family].queueFlags & vk::QueueFlagBits::eCompute)) ++family;
    require(family < queues.size(), "No compute queue");float priority = 1;
    vk::DeviceQueueCreateInfo qi{.queueFamilyIndex = family, .queueCount = 1, .pQueuePriorities = &priority};
    vk::raii::Device device(physical,{.pNext = &subgroup_features, .queueCreateInfoCount = 1, .pQueueCreateInfos = &qi, .pEnabledFeatures = &features});
    auto queue = device.getQueue(family,0);
    vk_allocator allocator({.physicalDevice = *physical, .device = *device, .instance = *instance, .vulkanApiVersion = VK_API_VERSION_1_3},false);
    auto shader = device.createShaderModule({.codeSize = spirv.size() * sizeof(uint32_t), .pCode = spirv.data()});
    std::cout << "GPU: " << physical.getProperties().deviceName << '\n';
    for (const auto & test : {TestCase{"identity-no-alpha",32,24,false,false,false}, {"identity-alpha",32,24,false,false,true},
             {"flipped-alpha",32,24,false,true,true}, {"foveated-alpha",80,64,true,false,true}, {"foveated-flipped-alpha",96,72,true,true,true},
             {"legacy420-identity",32,24,false,false,true,0}, {"legacy420-foveated-flipped",80,64,true,true,true,0},
             {"pyrowave444-identity",32,24,false,false,true,1}, {"pyrowave444-foveated-flipped",80,64,true,true,true,1}})
        run_case(device,queue,shader,family,test);
}
catch (const std::exception & e) { std::cerr << e.what() << '\n';return 1; }
