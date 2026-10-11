/*
 * WiVRn VR streaming
 * Copyright (C) 2025  Patrick Nicolas <patricknicolas@laposte.net>
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 */

#include "video_encoder_pyrowave.h"
#include "encoder_settings.h"
#include <algorithm>
#include <cmath>
#include <limits>
#include <mutex>
#include <stdexcept>

namespace wivrn
{
namespace
{
// PyroWave frames are independent; packet loss never requires an IDR request.
class independent_frames : public idr_handler
{
    void on_feedback(const from_headset::feedback &) override {}
    void reset() override {}
    bool should_skip(uint64_t) override { return false; }
};
buffer_allocation readback(vk::raii::Device & device, size_t size)
{
    return {device, {.size = size, .usage = vk::BufferUsageFlagBits::eStorageBuffer},
        {.flags = VMA_ALLOCATION_CREATE_HOST_ACCESS_RANDOM_BIT, .usage = VMA_MEMORY_USAGE_AUTO,
         .requiredFlags = VK_MEMORY_PROPERTY_HOST_VISIBLE_BIT}, "PyroWave readback"};
}
}
video_encoder_pyrowave::video_encoder_pyrowave(vk_bundle & vk, const encoder_settings & settings, uint8_t stream_idx) :
    video_encoder(vk, stream_idx, vk.queue.family_index, settings, std::make_unique<independent_frames>(), true),
    bundle(vk), bitrate(settings.bitrate), fps(settings.fps)
{
    if (settings.codec != pyrowave || settings.bit_depth != 8)
        throw std::runtime_error("PyroWave requires its own codec and 8-bit input");
    const auto & f = vk.feat;
    if (!f.get<vk::PhysicalDeviceFeatures2>().features.shaderInt16 ||
        !f.get<vk::PhysicalDeviceVulkan11Features>().storageBuffer16BitAccess ||
        !f.get<vk::PhysicalDeviceVulkan12Features>().storageBuffer8BitAccess ||
        !f.get<vk::PhysicalDeviceVulkan13Features>().subgroupSizeControl ||
        !f.get<vk::PhysicalDeviceVulkan13Features>().computeFullSubgroups ||
        !f.get<vk::PhysicalDeviceVulkan13Features>().maintenance4)
        throw std::runtime_error("GPU lacks the Vulkan features required by experimental PyroWave");
    for (auto & slot : state)
    {
        slot.encoder = std::make_unique<PyroWave::Encoder>(vk.physical_device, vk.device,
            settings.width, settings.height, PyroWave::ChromaSubsampling::Chroma420);
        slot.pool = vk.device.createCommandPool({.flags = vk::CommandPoolCreateFlagBits::eResetCommandBuffer,
                                                .queueFamilyIndex = vk.queue.family_index});
        slot.command = std::move(vk.device.allocateCommandBuffers({.commandPool = *slot.pool, .commandBufferCount = 1})[0]);
        slot.fence = vk.device.createFence({.flags = vk::FenceCreateFlagBits::eSignaled});
        slot.meta = readback(vk.device, slot.encoder->get_meta_required_size());
    }
}
video_encoder_pyrowave::~video_encoder_pyrowave()
{
    // Keep resources alive through any last submitted work on shutdown.
    for (auto & slot : state)
        if (*slot.fence) (void)bundle.device.waitForFences(*slot.fence, true, UINT64_MAX);
}
void video_encoder_pyrowave::present_image(vk::Image input, vk::SemaphoreSubmitInfo wait,
                                          uint8_t slot_index, uint64_t)
{
    auto & slot = state[slot_index];
    if (bundle.device.waitForFences(*slot.fence, true, 1'000'000'000) != vk::Result::eSuccess)
        throw std::runtime_error("PyroWave GPU timeout");
    if (auto b = pending_bitrate.exchange(0)) bitrate = b;
    if (auto f = pending_framerate.exchange(0); f > 0 && std::isfinite(f)) fps = f;
    if (!(fps > 0) || !std::isfinite(fps)) throw std::runtime_error("Invalid PyroWave frame rate");
    const size_t target = std::clamp<size_t>(bitrate / (8.0 * fps), 1024, 8 * 1024 * 1024);
    const size_t capacity = target + 2 * slot.meta.info().size;
    if (!slot.output || slot.output.info().size < capacity) slot.output = readback(bundle.device, capacity);
    for (uint32_t plane = 0; plane < 3; ++plane)
        slot.views[plane] = bundle.device.createImageView({
            .image = input, .viewType = vk::ImageViewType::e2D,
            .format = plane == 0 ? vk::Format::eR8Unorm : vk::Format::eR8G8Unorm,
            .components = {.r = plane == 2 ? vk::ComponentSwizzle::eG : vk::ComponentSwizzle::eIdentity},
            .subresourceRange = {.aspectMask = plane == 0 ? vk::ImageAspectFlagBits::ePlane0 : vk::ImageAspectFlagBits::ePlane1,
                                .levelCount = 1, .baseArrayLayer = stream_idx, .layerCount = 1}});
    slot.command.reset();
    slot.command.begin({.flags = vk::CommandBufferUsageFlagBits::eOneTimeSubmit});
    PyroWave::Encoder::BitstreamBuffers buffers{
        .meta = {.buffer = slot.meta, .size = slot.meta.info().size},
        .bitstream = {.buffer = slot.output, .size = slot.output.info().size}, .target_size = target};
    if (!slot.encoder->encode(slot.command, {*slot.views[0], *slot.views[1], *slot.views[2]}, buffers))
        throw std::runtime_error("PyroWave encode failed");
    vk::MemoryBarrier barrier{.srcAccessMask = vk::AccessFlagBits::eShaderWrite, .dstAccessMask = vk::AccessFlagBits::eHostRead};
    slot.command.pipelineBarrier(vk::PipelineStageFlagBits::eComputeShader, vk::PipelineStageFlagBits::eHost, {}, barrier, {}, {});
    slot.command.end();
    wait.stageMask = vk::PipelineStageFlagBits2::eComputeShader;
    vk::CommandBufferSubmitInfo command{.commandBuffer = *slot.command};
    vk::SubmitInfo2 submit{.waitSemaphoreInfoCount = 1, .pWaitSemaphoreInfos = &wait,
                          .commandBufferInfoCount = 1, .pCommandBufferInfos = &command};
    bundle.device.resetFences(*slot.fence);
    std::lock_guard lock(bundle.queue.mutex);
    bundle.queue.queue.submit2(submit, *slot.fence);
}
std::optional<video_encoder::data> video_encoder_pyrowave::encode(uint8_t slot_index, uint64_t)
{
    auto & slot = state[slot_index];
    if (bundle.device.waitForFences(*slot.fence, true, 1'000'000'000) != vk::Result::eSuccess)
        throw std::runtime_error("PyroWave GPU timeout");
    auto meta = static_cast<const PyroWave::BitstreamPacket *>(slot.meta.map());
    auto encoded = slot.output.map();
    vmaInvalidateAllocation(vk_allocator::instance(), slot.meta, 0, VK_WHOLE_SIZE);
    vmaInvalidateAllocation(vk_allocator::instance(), slot.output, 0, VK_WHOLE_SIZE);
    size_t size = sizeof(PyroWave::BitstreamSequenceHeader);
    const size_t words = slot.output.info().size / sizeof(uint32_t);
    for (size_t i = 0; i < slot.meta.info().size / sizeof(*meta); ++i)
    {
        if (meta[i].offset_u32 > words || meta[i].num_words > words - meta[i].offset_u32)
            throw std::runtime_error("Invalid PyroWave output block bounds");
        size += size_t(meta[i].num_words) * sizeof(uint32_t);
        if (size > 64 * 1024 * 1024) throw std::runtime_error("PyroWave output exceeds frame limit");
    }
    auto output = std::make_shared<std::vector<uint8_t>>(size);
    std::vector<PyroWave::Encoder::Packet> packets(slot.encoder->compute_num_packets(meta, size));
    slot.encoder->packetize(packets.data(), size, output->data(), output->size(), meta, encoded);
    return data{.encoder = this, .span = *output, .mem = output};
}
}
