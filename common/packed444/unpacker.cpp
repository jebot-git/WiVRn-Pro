// SPDX-License-Identifier: GPL-3.0-or-later
#include "unpacker.h"
#include "wivrn-packed444_shaders.h"

#include <array>
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace wivrn::packed444
{
namespace
{
struct constants
{
    int32_t width, height;
    int32_t imported_width, imported_height;
};
static_assert(sizeof(constants) == 16);
}

Unpacker::Unpacker(vk::raii::Device & device, vk::Extent2D logical_extent,
                   vk::Sampler raw_identity_sampler, uint32_t max_bindings) :
    device(device), extent(logical_extent)
{
    if (!extent.width || !extent.height || (extent.width & 1) || (extent.height & 1) ||
        extent.width > uint32_t(std::numeric_limits<int32_t>::max()) / 2 ||
        extent.height > uint32_t(std::numeric_limits<int32_t>::max()) ||
        !raw_identity_sampler || !max_bindings || max_bindings > 128)
        throw std::invalid_argument("Invalid packed444 unpacker dimensions, sampler or binding limit");

    std::array<vk::DescriptorSetLayoutBinding, 2> bindings{{
        {.binding = 0, .descriptorType = vk::DescriptorType::eCombinedImageSampler,
         .descriptorCount = 1, .stageFlags = vk::ShaderStageFlagBits::eCompute,
         .pImmutableSamplers = &raw_identity_sampler},
        {.binding = 1, .descriptorType = vk::DescriptorType::eStorageImage,
         .descriptorCount = 1, .stageFlags = vk::ShaderStageFlagBits::eCompute},
    }};
    descriptor_layout = device.createDescriptorSetLayout({.bindingCount = bindings.size(), .pBindings = bindings.data()});
    // A converted YCbCr combined sampler can consume multiple descriptors.
    // Reserve 16 per binding, exceeding the three planes of supported 420 AHBs.
    std::array<vk::DescriptorPoolSize, 2> sizes{{
        {.type = vk::DescriptorType::eCombinedImageSampler, .descriptorCount = max_bindings * 16},
        {.type = vk::DescriptorType::eStorageImage, .descriptorCount = max_bindings},
    }};
    descriptor_pool = device.createDescriptorPool({
        .flags = vk::DescriptorPoolCreateFlagBits::eFreeDescriptorSet,
        .maxSets = max_bindings, .poolSizeCount = sizes.size(), .pPoolSizes = sizes.data()});
    vk::PushConstantRange range{.stageFlags = vk::ShaderStageFlagBits::eCompute,
                                .offset = 0, .size = sizeof(constants)};
    pipeline_layout = device.createPipelineLayout({.setLayoutCount = 1, .pSetLayouts = &*descriptor_layout,
        .pushConstantRangeCount = 1, .pPushConstantRanges = &range});
    const auto & spirv = ::packed444::shaders.at("unpack");
    auto shader = device.createShaderModule({.codeSize = spirv.size() * sizeof(uint32_t), .pCode = spirv.data()});
    pipeline = device.createComputePipeline(nullptr, {
        .stage = {.stage = vk::ShaderStageFlagBits::eCompute, .module = *shader, .pName = "main"},
        .layout = *pipeline_layout});
}

Unpacker::Binding Unpacker::create_binding(vk::ImageView imported_view, vk::ImageView rgba_view,
                                          vk::ImageLayout imported_layout)
{
    if (!imported_view || !rgba_view)
        throw std::invalid_argument("Null packed444 input or output image view");
    Binding binding{std::move(device.allocateDescriptorSets({.descriptorPool = *descriptor_pool,
        .descriptorSetCount = 1, .pSetLayouts = &*descriptor_layout})[0])};
    std::array<vk::DescriptorImageInfo, 2> images{{
        {.imageView = imported_view, .imageLayout = imported_layout},
        {.imageView = rgba_view, .imageLayout = vk::ImageLayout::eGeneral},
    }};
    std::array<vk::WriteDescriptorSet, 2> writes{};
    for (uint32_t i = 0; i < writes.size(); ++i)
        writes[i] = {.dstSet = *binding.descriptor, .dstBinding = i, .descriptorCount = 1,
            .descriptorType = i == 0 ? vk::DescriptorType::eCombinedImageSampler : vk::DescriptorType::eStorageImage,
            .pImageInfo = &images[i]};
    device.updateDescriptorSets(writes, {});
    return binding;
}

void Unpacker::record(vk::raii::CommandBuffer & command, const Binding & binding,
                      vk::Extent2D imported_extent) const
{
    if (imported_extent.width < extent.width * 2 || imported_extent.height < extent.height ||
        imported_extent.width > uint32_t(std::numeric_limits<int32_t>::max()) ||
        imported_extent.height > uint32_t(std::numeric_limits<int32_t>::max()) || !*binding.descriptor)
        throw std::invalid_argument("Packed444 hardware buffer is smaller than its coded frame");
    constants push{int32_t(extent.width), int32_t(extent.height),
                   int32_t(imported_extent.width), int32_t(imported_extent.height)};
    command.bindPipeline(vk::PipelineBindPoint::eCompute, *pipeline);
    command.bindDescriptorSets(vk::PipelineBindPoint::eCompute, *pipeline_layout, 0, *binding.descriptor, {});
    command.pushConstants<constants>(*pipeline_layout, vk::ShaderStageFlagBits::eCompute, 0, push);
    command.dispatch((extent.width + 7) / 8, (extent.height + 7) / 8, 1);
}
}
