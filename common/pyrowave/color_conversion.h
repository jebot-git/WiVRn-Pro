// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "pyrowave_common.h"
namespace PyroWave {
// Persistent full-resolution Y/Cb/Cr scratch planes. All pixel work stays on the GPU.
class ColorConversion {
    vk::raii::Device & device;
    std::array<image_allocation, 3> planes;
    std::array<vk::raii::ImageView, 3> views{nullptr, nullptr, nullptr};
    vk::raii::Sampler sampler = nullptr;
    vk::raii::DescriptorPool pool = nullptr;
    vk::raii::DescriptorSetLayout ds_layout = nullptr;
    vk::raii::PipelineLayout layout = nullptr;
    vk::raii::Pipeline pipeline = nullptr;
    vk::DescriptorSet set{};
    vk::Extent2D extent;
    bool initialized = false;
public:
    ColorConversion(vk::raii::Device &, vk::Extent2D);
    std::array<vk::ImageView, 3> begin(vk::raii::CommandBuffer &);
    void convert(vk::raii::CommandBuffer &, vk::ImageView rgba);
};
}
