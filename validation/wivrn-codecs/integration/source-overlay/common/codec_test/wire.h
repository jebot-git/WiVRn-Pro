// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include <array>
#include <cstring>
#include <span>
#include <stdexcept>
#include <vector>

namespace wivrn::codec_test {
constexpr size_t header_size = 48, max_payload = 16*1024*1024;
enum class Kind : uint32_t { hevc_carrier=1, vr444=2, raw_rgba=3, rrb32=4 };
inline constexpr auto crc_table=[] {
    std::array<uint32_t,256> table{};
    for(unsigned n=0;n<256;++n){uint32_t c=n;for(int i=0;i<8;++i)c=(c>>1)^(0xedb88320u & (0u-(c&1)));table[n]=c;}
    return table;
}();
inline uint32_t crc(std::span<const uint8_t> data) {
    uint32_t c=~0u;
    for(auto b:data)c=(c>>8)^crc_table[(c^b)&255];
    return ~c;
}
inline uint64_t get(std::span<const uint8_t> p,size_t at,size_t n) {
    if(at+n>p.size()) throw std::runtime_error("truncated test envelope");
    uint64_t v=0; for(size_t i=0;i<n;++i) v|=uint64_t(p[at+i])<<(i*8); return v;
}
inline void put(std::vector<uint8_t>& p,size_t at,uint64_t v,size_t n) {
    for(size_t i=0;i<n;++i) p.at(at+i)=uint8_t(v>>(8*i));
}
// Dedicated test-build extension. Older readers reject nonzero flags; writers
// retain the byte-identical legacy envelope unless explicitly opted in.
constexpr uint32_t flag_lz4 = 1;
struct Header { Kind kind; uint32_t width,height,eye; uint64_t frame,warp; uint32_t flags=0; };
inline void validate(const Header& h) {
    if((h.kind!=Kind::hevc_carrier && h.kind!=Kind::vr444 && h.kind!=Kind::raw_rgba && h.kind!=Kind::rrb32)||h.width<2||h.height<2||h.width>4096||h.height>4096||uint64_t(h.width)*h.height>4194304||h.eye>1||(h.flags & ~flag_lz4)||(h.flags && h.kind!=Kind::vr444))
        throw std::runtime_error("invalid test envelope dimensions/kind/eye");
}
inline std::vector<uint8_t> pack(const Header& h,std::span<const uint8_t> data) {
    validate(h); if(data.empty()||data.size()>max_payload) throw std::runtime_error("test payload size");
    std::vector<uint8_t> p(header_size+data.size());
    put(p,0,0x31544357,4); put(p,4,uint32_t(h.kind),4); put(p,8,h.width,4);put(p,12,h.height,4);
    put(p,16,h.eye,4);put(p,20,data.size(),4);put(p,24,h.frame,8);put(p,32,h.warp,8);put(p,40,crc(data),4);
    put(p,44,h.flags,4);
    std::memcpy(p.data()+header_size,data.data(),data.size());return p;
}
inline Header unpack(std::span<const uint8_t> p) {
    if(p.size()<header_size||get(p,0,4)!=0x31544357||get(p,20,4)>max_payload||get(p,20,4)==0||p.size()!=header_size+get(p,20,4))
        throw std::runtime_error("invalid test envelope framing");
    Header h{Kind(get(p,4,4)),uint32_t(get(p,8,4)),uint32_t(get(p,12,4)),uint32_t(get(p,16,4)),get(p,24,8),get(p,32,8),uint32_t(get(p,44,4))};
    validate(h); if(crc(p.subspan(header_size))!=get(p,40,4)) throw std::runtime_error("test payload checksum");return h;
}
} // namespace
