// SPDX-License-Identifier: MIT
#pragma once
#include "codec.hpp"
#include <memory>
#include <span>
namespace vr444 {
struct GpuResult { Frame frame; std::vector<uint32_t> rgba; double encode_ms,decode_ms,submit_ms; std::string device; bool software; std::span<const uint8_t> packed; double input_copy_ms=0, output_copy_pack_ms=0; };
// One instance per producer thread. GPU resources are fixed-size and reused.
class GpuSession {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    GpuSession(unsigned width,unsigned height,const std::string& shaders,const std::string& kernel="baseline",unsigned device_index=0);
    ~GpuSession();
    GpuSession(const GpuSession&)=delete;
    GpuSession& operator=(const GpuSession&)=delete;
    GpuResult encode(const std::vector<uint32_t>& rgba,unsigned q,const std::vector<uint32_t>& tile_q={});
    GpuResult encode_packed(std::span<const uint32_t> rgba,unsigned q,const std::vector<uint32_t>& tile_q={});
    GpuResult decode(const Frame& frame);
    GpuResult roundtrip(const std::vector<uint32_t>& rgba,unsigned q,const std::vector<uint32_t>& tile_q={});
};
GpuResult gpu_roundtrip(const std::vector<uint32_t>& rgba, unsigned w,unsigned h,unsigned q,const std::string& shaders,const Frame* decode_only=nullptr,const std::vector<uint32_t>& tile_q={});
}
