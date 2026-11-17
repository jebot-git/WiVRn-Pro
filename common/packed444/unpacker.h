// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include <vulkan/vulkan_raii.hpp>

namespace wivrn::packed444
{
// GPU unpack of packed444_v1: coded Y holds [Y | Cb], coded Cb/Cr hold
// alternating rows of full-resolution Cr. The imported image view and sampler
// must share an RGB_IDENTITY YCbCr conversion, with nearest luma/chroma filters.
class Unpacker
{
    vk::raii::Device & device;
    vk::Extent2D extent;
    vk::raii::DescriptorSetLayout descriptor_layout = nullptr;
    vk::raii::DescriptorPool descriptor_pool = nullptr;
    vk::raii::PipelineLayout pipeline_layout = nullptr;
    vk::raii::Pipeline pipeline = nullptr;

public:
    // Each Binding is immutable after creation. Keep it, its imported AImage,
    // views and this Unpacker alive until the GPU submission has completed.
    struct Binding
    {
        vk::raii::DescriptorSet descriptor = nullptr;
    };

    Unpacker(vk::raii::Device &, vk::Extent2D logical_extent,
             vk::Sampler raw_identity_sampler, uint32_t max_bindings = 16);

    Binding create_binding(vk::ImageView imported_view, vk::ImageView rgba_view,
                           vk::ImageLayout imported_layout = vk::ImageLayout::eGeneral);

    // Caller makes imported input shader-readable and RGBA output GENERAL /
    // shader-writable, then makes output visible to its subsequent consumer.
    // imported_extent includes any actual hardware-buffer allocation padding.
    void record(vk::raii::CommandBuffer &, const Binding &, vk::Extent2D imported_extent) const;
};
}
