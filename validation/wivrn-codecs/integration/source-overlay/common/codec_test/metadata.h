// SPDX-License-Identifier: MIT
#pragma once
#include "wivrn_packets.h"
#include <bit>
#include <cstdlib>
#include <string_view>
namespace wivrn::codec_test {
inline bool enabled() {
#ifdef WIVRN_CODEC_TESTS
    const char* mode=std::getenv("WIVRN_CODEC_TEST");
    return mode && (std::string_view(mode)=="vulkan-vr444"||std::string_view(mode)=="hevc-carrier"||std::string_view(mode)=="raw-rgba");
#else
    return false;
#endif
}
// Canonical field-by-field hash: never hash padding or vector pointers.
inline uint64_t warp_hash(const to_headset::video_stream_data_shard::view_info_t& v) {
    uint64_t h=14695981039346656037ULL;
    auto u=[&](uint64_t n){for(int i=0;i<8;++i){h^=uint8_t(n>>(i*8));h*=1099511628211ULL;}};
    auto f=[&](float n){u(std::bit_cast<uint32_t>(n));};
    u(v.display_time);u(v.alpha);
    for(unsigned eye=0;eye<2;++eye){
        auto p=v.pose[eye];f(p.orientation.x);f(p.orientation.y);f(p.orientation.z);f(p.orientation.w);
        f(p.position.x);f(p.position.y);f(p.position.z);
        auto a=v.fov[eye];f(a.angleLeft);f(a.angleRight);f(a.angleUp);f(a.angleDown);
        for(auto* axis:{&v.foveation[eye].x,&v.foveation[eye].y}){u(axis->size());for(auto n:*axis)u(n);}
    }
    return h;
}
} // namespace
