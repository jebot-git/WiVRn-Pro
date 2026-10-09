// SPDX-License-Identifier: MIT
#pragma once
#include "codec.hpp"
namespace vr444 {
struct GpuResult { Frame frame; std::vector<uint32_t> rgba; double encode_ms,decode_ms,submit_ms; std::string device; bool software; };
GpuResult gpu_roundtrip(const std::vector<uint32_t>& rgba, unsigned w,unsigned h,unsigned q,const std::string& shaders,const Frame* decode_only=nullptr,const std::vector<uint32_t>& tile_q={});
}
