// SPDX-License-Identifier: MIT
#pragma once
#include "baseline/codec.hpp"
#include <bit>
#include <cstring>
#include <span>
#include <stdexcept>
#include <vector>

namespace latency_host {
inline bool aliases(std::span<const uint32_t> input, const std::vector<uint8_t>& output) {
    if (input.empty() || !output.capacity()) return false;
    auto x=reinterpret_cast<uintptr_t>(input.data()), y=reinterpret_cast<uintptr_t>(output.data());
    return x<=y ? y-x<input.size_bytes() : x-y<output.capacity();
}
inline void pack_mapped(unsigned width, unsigned height, unsigned q,
                        std::span<const uint32_t> slots, std::span<const uint32_t> lengths,
                        std::span<const uint32_t> tile_q, std::vector<uint8_t>& output) {
    auto require=[](bool ok) { if(!ok) throw std::runtime_error("invalid mapped pack input"); };
    require(width && height && width<=4096 && height<=4096 && uint64_t(width)*height<=8388608);
    const size_t count=size_t((width+15)/16)*((height+15)/16);
    require(q>=1 && q<=1024 && lengths.size()==count && slots.size()==count*vr444::SLOT_WORDS);
    require(tile_q.empty() || tile_q.size()==count);
    require(!aliases(slots,output) && !aliases(lengths,output) && !aliases(tile_q,output));
    size_t bytes=24+count*(tile_q.empty()?2:4);
    for(size_t tile=0;tile<count;++tile) {
        require(lengths[tile]>=3 && lengths[tile]<=vr444::SLOT_WORDS);
        require(tile_q.empty() || (tile_q[tile]>=1 && tile_q[tile]<=1024));
        bytes+=size_t(lengths[tile])*4;
    }
    // This codec-test experiment keeps the outer test payload bound explicit.
    require(bytes<=16u*1024*1024);
    output.resize(bytes);
    size_t at=0;
    auto put=[&](uint32_t value,unsigned n) {for(unsigned i=0;i<n;++i)output[at++]=uint8_t(value>>(i*8));};
    put(0x34345256,4);put(tile_q.empty()?1:2,4);put(width,4);put(height,4);put(q,4);put(count,4);
    for(size_t tile=0;tile<count;++tile) {
        put(lengths[tile],2);if(!tile_q.empty())put(tile_q[tile],2);
        if constexpr(std::endian::native==std::endian::little) {
            std::memcpy(output.data()+at,slots.data()+tile*vr444::SLOT_WORDS,size_t(lengths[tile])*4);
            at+=size_t(lengths[tile])*4;
        } else {
            for(unsigned word=0;word<lengths[tile];++word)put(slots[tile*vr444::SLOT_WORDS+word],4);
        }
    }
}
} // namespace latency_host
