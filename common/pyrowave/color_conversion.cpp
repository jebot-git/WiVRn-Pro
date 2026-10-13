// SPDX-License-Identifier: GPL-3.0-or-later
#include "color_conversion.h"
namespace PyroWave {
ColorConversion::ColorConversion(vk::raii::Device & device, vk::Extent2D extent) : device(device), extent(extent)
{
    for (uint32_t c = 0; c < 3; ++c) {
        planes[c] = image_allocation(device, {.imageType = vk::ImageType::e2D, .format = vk::Format::eR8G8B8A8Unorm,
            .extent = {(extent.width + 1) / 2, (extent.height + 1) / 2, 1}, .mipLevels = 1, .arrayLayers = 1,
            .usage = vk::ImageUsageFlagBits::eStorage | vk::ImageUsageFlagBits::eSampled},
            {.usage = VMA_MEMORY_USAGE_AUTO}, "PyroWave full-chroma plane");
        views[c] = device.createImageView({.image = planes[c], .viewType = vk::ImageViewType::e2D, .format = vk::Format::eR8G8B8A8Unorm,
            .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}});
    }
    sampler = device.createSampler({.magFilter = vk::Filter::eNearest, .minFilter = vk::Filter::eNearest,
        .addressModeU = vk::SamplerAddressMode::eClampToEdge, .addressModeV = vk::SamplerAddressMode::eClampToEdge,
        .addressModeW = vk::SamplerAddressMode::eClampToEdge});
    std::array<vk::DescriptorSetLayoutBinding, 4> bindings{};
    for (uint32_t i = 0; i < 4; ++i) bindings[i] = {.binding = i,
        .descriptorType = i == 3 ? vk::DescriptorType::eStorageImage : vk::DescriptorType::eCombinedImageSampler,
        .descriptorCount = 1, .stageFlags = vk::ShaderStageFlagBits::eCompute};
    ds_layout = device.createDescriptorSetLayout({.bindingCount = bindings.size(), .pBindings = bindings.data()});
    layout = device.createPipelineLayout({.setLayoutCount = 1, .pSetLayouts = &*ds_layout});
    std::array<vk::DescriptorPoolSize, 2> sizes{{{.type = vk::DescriptorType::eCombinedImageSampler, .descriptorCount = 3},
        {.type = vk::DescriptorType::eStorageImage, .descriptorCount = 1}}};
    pool = device.createDescriptorPool({.maxSets = 1, .poolSizeCount = sizes.size(), .pPoolSizes = sizes.data()});
    set = device.allocateDescriptorSets({.descriptorPool = *pool, .descriptorSetCount = 1, .pSetLayouts = &*ds_layout})[0].release();
    auto shader = load_shader(device, "ycbcr444_to_rgba");
    pipeline = device.createComputePipeline(nullptr, {.stage = {.stage = vk::ShaderStageFlagBits::eCompute,
        .module = *shader, .pName = "main"}, .layout = *layout});
}
std::array<vk::ImageView, 3> ColorConversion::begin(vk::raii::CommandBuffer & cmd)
{
    std::array<vk::ImageMemoryBarrier, 3> barriers{};
    for (uint32_t c = 0; c < 3; ++c) barriers[c] = {
        .srcAccessMask = initialized ? vk::AccessFlagBits::eShaderRead : vk::AccessFlagBits::eNone,
        .dstAccessMask = vk::AccessFlagBits::eShaderWrite,
        .oldLayout = initialized ? vk::ImageLayout::eGeneral : vk::ImageLayout::eUndefined,
        .newLayout = vk::ImageLayout::eGeneral, .srcQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED,
        .dstQueueFamilyIndex = VK_QUEUE_FAMILY_IGNORED, .image = planes[c],
        .subresourceRange = {.aspectMask = vk::ImageAspectFlagBits::eColor, .levelCount = 1, .layerCount = 1}};
    cmd.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eComputeShader, {}, {}, {}, barriers);
    initialized = true;
    return {*views[0], *views[1], *views[2]};
}
void ColorConversion::convert(vk::raii::CommandBuffer & cmd, vk::ImageView rgba)
{
    vk::MemoryBarrier barrier{.srcAccessMask = vk::AccessFlagBits::eShaderWrite, .dstAccessMask = vk::AccessFlagBits::eShaderRead};
    cmd.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eComputeShader, {}, barrier, {}, {});
    std::array<vk::DescriptorImageInfo, 4> images{};
    std::array<vk::WriteDescriptorSet, 4> writes{};
    for (uint32_t i = 0; i < 4; ++i) {
        images[i] = {.sampler = i == 3 ? vk::Sampler{} : *sampler, .imageView = i == 3 ? rgba : *views[i], .imageLayout = vk::ImageLayout::eGeneral};
        writes[i] = {.dstSet = set, .dstBinding = i, .descriptorCount = 1,
            .descriptorType = i == 3 ? vk::DescriptorType::eStorageImage : vk::DescriptorType::eCombinedImageSampler, .pImageInfo = &images[i]};
    }
    device.updateDescriptorSets(writes, {});
    cmd.bindPipeline(vk::PipelineBindPoint::eCompute, *pipeline);
    cmd.bindDescriptorSets(vk::PipelineBindPoint::eCompute, *layout, 0, set, {});
    cmd.dispatch((extent.width + 7) / 8, (extent.height + 7) / 8, 1);
}
}
