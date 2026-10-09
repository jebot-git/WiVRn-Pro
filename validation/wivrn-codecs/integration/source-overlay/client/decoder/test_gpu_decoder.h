// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once

#include "codec_test/vr444/codec.hpp"
#include <vulkan/vulkan.h>
#include <memory>
#include <string>

namespace wivrn {
// Borrows the application's device. One instance belongs to one decoder worker.
// The caller must finish every submission using this instance before calling
// record_decode again or destroying it. In particular, recording also writes
// mapped input buffers; it must not overlap a prior pending GPU decode.
class test_gpu_decoder {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    test_gpu_decoder(VkPhysicalDevice physical, VkDevice device, uint32_t queue_family,
                     uint32_t width, uint32_t height, const std::string& shaders,
                     const std::string& kernel = "baseline");
    ~test_gpu_decoder();
    test_gpu_decoder(const test_gpu_decoder&) = delete;
    test_gpu_decoder& operator=(const test_gpu_decoder&) = delete;

    // Validates the frame before updating input storage, then appends compute
    // and a compute-write -> transfer-read barrier to the recording command.
    // Does not begin/end the command buffer, submit, or wait for GPU work.
    void record_decode(VkCommandBuffer command, const vr444::Frame& frame);
    // Packed RGBA8, width*height*4 bytes. STORAGE_BUFFER | TRANSFER_SRC usage.
    VkBuffer output() const;
};
}
