// SPDX-License-Identifier: MIT
#pragma once
#include "vendor/lz4.h"
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstring>
#include <span>
#include <stdexcept>
#include <vector>
#if defined(__aarch64__) && (defined(__linux__) || defined(__ANDROID__))
#include <arm_acle.h>
#include <sys/auxv.h>
#include <asm/hwcap.h>
#endif

namespace wivrn::codec_test::compression::detail {
using Bytes = std::vector<uint8_t>;
using View = std::span<const uint8_t>;
constexpr uint32_t wire_limit = 16u * 1024 * 1024;
constexpr uint32_t input_limit = wire_limit - 24;
inline void require(bool b) { if (!b) throw std::runtime_error("invalid WPC1 input"); }
inline uint32_t u32(View b, size_t p) {
    require(p <= b.size() && b.size()-p >= 4);
    return uint32_t(b[p]) | uint32_t(b[p+1])<<8 | uint32_t(b[p+2])<<16 | uint32_t(b[p+3])<<24;
}
inline void put(Bytes &b, uint32_t v) { for (unsigned i=0;i<4;++i) b.push_back(uint8_t(v>>(8*i))); }
inline uint32_t crc_table(View b) {
    static const auto table = [] {
        std::array<uint32_t,256> t{};
        for(uint32_t i=0;i<256;++i) { uint32_t c=i; for(int k=0;k<8;++k) c=(c>>1)^((c&1)?0xedb88320u:0); t[i]=c; }
        return t;
    }();
    uint32_t c=~0u;
    for(auto v:b) c=table[(c^v)&255]^(c>>8);
    return ~c;
}
#if defined(__aarch64__) && (defined(__linux__) || defined(__ANDROID__))
__attribute__((target("crc"))) inline uint32_t crc_arm(View b) {
    uint32_t c=~0u; size_t p=0;
    for(;b.size()-p>=8;p+=8) { uint64_t word; std::memcpy(&word,b.data()+p,8); c=__crc32d(c,word); }
    for(;p<b.size();++p)c=__crc32b(c,b[p]);
    return ~c;
}
inline bool arm_crc_enabled() { static bool enabled=(getauxval(AT_HWCAP)&HWCAP_CRC32)!=0; return enabled; }
#else
inline bool arm_crc_enabled() { return false; }
#endif
inline uint32_t crc(View b) {
#if defined(__aarch64__) && (defined(__linux__) || defined(__ANDROID__))
    if(arm_crc_enabled())return crc_arm(b);
#endif
    return crc_table(b);
}
struct Workspace { Bytes wire, scratch, decoded; };
inline bool overlaps(View a, View b) {
    if(a.empty()||b.empty())return false;
    auto x=reinterpret_cast<uintptr_t>(a.data()),y=reinterpret_cast<uintptr_t>(b.data());
    return x<=y?y-x<a.size():x-y<b.size();
}
inline void header(Bytes &b, uint8_t method, uint32_t n, uint32_t block, uint32_t count, uint32_t checksum) {
    b.clear(); b.insert(b.end(), {'W','P','C','1',1,method,0,0});
    put(b,n); put(b,block); put(b,count); put(b,checksum);
}
// Reuses capacities after warmup. Allocation, framing, copies and CRC are included.
inline void encode(View raw, uint32_t block, Workspace &w) {
    require(!raw.empty() && raw.size()<=input_limit && block>=256 && block<=wire_limit);
    require(!overlaps(raw,w.wire)&&!overlaps(raw,w.scratch));
    auto checksum=crc(raw);
    header(w.wire,1,uint32_t(raw.size()),block,uint32_t((raw.size()+block-1)/block),checksum);
    w.scratch.resize(LZ4_compressBound(int(std::min<size_t>(raw.size(),block))));
    for(size_t at=0; at<raw.size();) {
        auto n=uint32_t(std::min<size_t>(block,raw.size()-at));
        int packed=LZ4_compress_default(reinterpret_cast<const char*>(raw.data()+at),reinterpret_cast<char*>(w.scratch.data()),int(n),int(w.scratch.size()));
        require(packed>0);
        bool compressed=uint32_t(packed)<n;
        uint32_t stored=compressed?uint32_t(packed):n;
        // Stop as soon as the accumulated framing cannot beat the whole raw frame.
        if (w.wire.size()+12+stored >= raw.size()+24) {
            header(w.wire,0,uint32_t(raw.size()),0,0,checksum);
            w.wire.insert(w.wire.end(),raw.begin(),raw.end()); return;
        }
        put(w.wire,compressed?1:0); put(w.wire,n); put(w.wire,stored);
        auto p=compressed?w.scratch.data():raw.data()+at;
        w.wire.insert(w.wire.end(),p,p+stored); at+=n;
    }
    require(w.wire.size()<=wire_limit);
}
inline void decode(View b, Workspace &w) {
    require(b.size()>24 && b.size()<=wire_limit);
    require(!overlaps(b,w.decoded));
    require(b[0]=='W'&&b[1]=='P'&&b[2]=='C'&&b[3]=='1'&&b[4]==1&&b[6]==0&&b[7]==0);
    uint32_t method=b[5], n=u32(b,8), block=u32(b,12), count=u32(b,16), checksum=u32(b,20);
    require(n>0 && n<=input_limit && method<=1);
    if(method==0) {
        require(block==0 && count==0 && b.size()==24+n);
        w.decoded.assign(b.begin()+24,b.end());
    } else {
        require(block>=256 && block<=wire_limit && count==(uint64_t(n)+block-1)/block);
        // Full framing validation before resizing output or invoking LZ4.
        size_t at=24; uint32_t left=n;
        for(uint32_t i=0;i<count;++i) {
            require(at<=b.size() && b.size()-at>=12);
            auto flags=u32(b,at), raw=u32(b,at+4), stored=u32(b,at+8); at+=12;
            require(flags<=1 && raw==std::min(block,left) && stored>0 && stored<=raw);
            require(flags || stored==raw);
            require(stored<=b.size()-at); at+=stored; left-=raw;
        }
        require(left==0 && at==b.size());
        w.decoded.resize(n); at=24; size_t out=0;
        for(uint32_t i=0;i<count;++i) {
            auto flags=u32(b,at), raw=u32(b,at+4), stored=u32(b,at+8); at+=12;
            if(flags) {
                auto got=LZ4_decompress_safe(reinterpret_cast<const char*>(b.data()+at),reinterpret_cast<char*>(w.decoded.data()+out),int(stored),int(raw));
                require(got==int(raw));
            } else std::memcpy(w.decoded.data()+out,b.data()+at,raw);
            at+=stored; out+=raw;
        }
    }
    require(crc(w.decoded)==checksum);
}
}
