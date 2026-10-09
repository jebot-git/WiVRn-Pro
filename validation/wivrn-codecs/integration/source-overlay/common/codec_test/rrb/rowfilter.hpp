// SPDX-License-Identifier: MIT
#pragma once
#include "wpc.hpp"
#include "simd.hpp"

// RRF1 is an independent exact RGB24 frame format, not PNG or production VR444.
namespace rowfilter {
using Bytes=std::vector<uint8_t>;
using View=std::span<const uint8_t>;
constexpr size_t max_pixels=4194304,header_bytes=32,max_body=max_pixels*3,max_wire=max_body+header_bytes;
enum class Policy {plain=1,up=2,adaptive=3};
inline void need(bool b){if(!b)throw std::runtime_error("invalid RRF1 frame");}
inline size_t pixels(uint32_t width,uint32_t height){need(width&&height&&width<=4096&&height<=4096&&uint64_t(width)*height<=max_pixels);return size_t(width)*height;}
struct Workspace {Bytes rgb,filtered,compressed,alternate,wire;};
inline bool alias(View input,const Bytes&buffer){
    if(input.empty()||!buffer.capacity())return false;
    auto a=reinterpret_cast<uintptr_t>(input.data()),b=reinterpret_cast<uintptr_t>(buffer.data());
    return a<=b?b-a<input.size():a-b<buffer.capacity();
}
inline bool alias(View input,const Workspace&w){return alias(input,w.rgb)||alias(input,w.filtered)||alias(input,w.compressed)||alias(input,w.alternate)||alias(input,w.wire);}
inline View bytes(std::span<const uint32_t> rgba){return {reinterpret_cast<const uint8_t*>(rgba.data()),rgba.size_bytes()};}
inline void put32(Bytes&b,size_t at,uint32_t v){for(unsigned i=0;i<4;++i)b[at+i]=uint8_t(v>>(8*i));}
inline void predict(View rgb,size_t stride,Bytes&filtered,bool simd=true){
    need(stride>0&&stride<=rgb.size()&&rgb.size()%stride==0);filtered.resize(rgb.size());
    std::copy_n(rgb.data(),stride,filtered.data());
    detail::subtract(rgb.subspan(stride),rgb.first(rgb.size()-stride),std::span(filtered).subspan(stride),simd);
}
inline void restore(Bytes&rgb,size_t stride,bool simd=true){
    need(stride>0&&stride<=rgb.size()&&rgb.size()%stride==0);
    for(size_t row=stride;row<rgb.size();row+=stride)
        detail::add_row(std::span(rgb).subspan(row,stride),View(rgb).subspan(row-stride,stride),simd);
}
inline View encode(std::span<const uint32_t>rgba,uint32_t width,uint32_t height,Policy policy,Workspace&w,bool simd=true){
    size_t count=pixels(width,height);need(rgba.size()==count&&!alias(bytes(rgba),w));
    need(policy==Policy::plain||policy==Policy::up||policy==Policy::adaptive);
    w.rgb.resize(count*3);
    detail::pack(rgba,w.rgb,simd);
    size_t bound=size_t(LZ4_compressBound(int(w.rgb.size())));w.compressed.resize(bound);
    View chosen=w.rgb;unsigned mode=0;
    auto attempt=[&](View input,Bytes&storage,unsigned candidate){
        storage.resize(bound);int n=LZ4_compress_default(reinterpret_cast<const char*>(input.data()),reinterpret_cast<char*>(storage.data()),int(input.size()),int(storage.size()));
        need(n>0);if(size_t(n)+64<=w.rgb.size()&&size_t(n)<chosen.size()){chosen=View(storage).first(size_t(n));mode=candidate;}
    };
    if(policy!=Policy::up)attempt(w.rgb,w.compressed,1);
    if(policy!=Policy::plain){predict(w.rgb,size_t(width)*3,w.filtered,simd);attempt(w.filtered,policy==Policy::up?w.compressed:w.alternate,2);}
    w.wire.assign(header_bytes,0);w.wire[0]='R';w.wire[1]='R';w.wire[2]='F';w.wire[3]='1';w.wire[4]=1;w.wire[5]=uint8_t(mode);
    put32(w.wire,8,width);put32(w.wire,12,height);put32(w.wire,16,uint32_t(chosen.size()));put32(w.wire,20,uint32_t(w.rgb.size()));
    put32(w.wire,24,wpc::crc(chosen));put32(w.wire,28,wpc::crc(View(w.wire).first(28)));
    w.wire.insert(w.wire.end(),chosen.begin(),chosen.end());return w.wire;
}
struct Shape {uint32_t width,height,mode;size_t raw_bytes;};
// LZ4_decompress_safe is memory-bounded but accepts offset zero in the vendored
// implementation. RRF1 additionally requires the documented block grammar.
inline void validate_lz4(View body,size_t raw){
    size_t at=0,produced=0,last_match=0;bool matched=false;
    auto length=[&](unsigned initial){size_t value=initial;if(initial==15){unsigned extra;
        do{need(at<body.size());extra=body[at++];need(value<=raw&&extra<=raw-value);value+=extra;}while(extra==255);}
        return value;};
    while(at<body.size()){
        uint8_t token=body[at++];size_t literals=length(token>>4);need(literals<=body.size()-at&&produced<=raw&&literals<=raw-produced);
        at+=literals;produced+=literals;
        if(at==body.size()){
            need(produced==raw&&(raw<5||literals>=5));
            need(!matched||(raw>=12&&last_match<=raw-12));return;
        }
        need(body.size()-at>=2);size_t offset=size_t(body[at])|(size_t(body[at+1])<<8);at+=2;
        need(offset>0&&offset<=produced);size_t match=length(token&15)+4;need(match<=raw-produced);
        last_match=produced;matched=true;produced+=match;
    }
    need(false); // Last sequence must terminate with literals, not a match.
}
inline Shape inspect(View wire){
    need(wire.size()>header_bytes&&wire.size()<=max_wire);
    need(wire[0]=='R'&&wire[1]=='R'&&wire[2]=='F'&&wire[3]=='1'&&wire[4]==1&&wire[5]<=2&&wire[6]==0&&wire[7]==0);
    Shape s{wpc::u32(wire,8),wpc::u32(wire,12),wire[5],0};s.raw_bytes=pixels(s.width,s.height)*3;
    uint32_t stored=wpc::u32(wire,16);need(stored==wire.size()-header_bytes&&wpc::u32(wire,20)==s.raw_bytes);
    need(s.mode?stored<s.raw_bytes:stored==s.raw_bytes);
    need(wpc::u32(wire,28)==wpc::crc(wire.first(28))&&wpc::u32(wire,24)==wpc::crc(wire.subspan(header_bytes)));
    if(s.mode)validate_lz4(wire.subspan(header_bytes),s.raw_bytes);
    return s;
}
// Returns a complete, bounded RGB-byte buffer in w.rgb. Mode2 still contains
// up residuals; modes0/1 contain RGB. An asynchronous consumer must retain an
// immutable copy/lease until completion. No GPU work may use failed/partial data.
inline Shape unpack_rgb(View wire,Workspace&w){
    need(!alias(wire,w));w.rgb.clear();
    try{auto s=inspect(wire);
        w.rgb.resize(s.raw_bytes);auto body=wire.subspan(header_bytes);
        if(s.mode){int n=LZ4_decompress_safe(reinterpret_cast<const char*>(body.data()),reinterpret_cast<char*>(w.rgb.data()),int(body.size()),int(w.rgb.size()));need(n==int(w.rgb.size()));}
        else std::copy(body.begin(),body.end(),w.rgb.begin());
        return s;
    }catch(...){w.rgb.clear();throw;}
}
// Complete input CRC/length validation and bounded LZ4 reconstruction happen
// before any caller-owned RGBA output is written. Input must stay immutable.
inline Shape decode_into(View wire,std::span<uint32_t>output,Workspace&w,bool simd=true){
    need(output.size()<=max_pixels&&!wpc::overlaps(wire,bytes(output))&&!alias(bytes(output),w));
    try{auto s=unpack_rgb(wire,w);need(output.size()==s.raw_bytes/3);
        if(s.mode==2)restore(w.rgb,size_t(s.width)*3,simd);
        detail::expand(w.rgb,output,simd);
        return s;
    }catch(...){w.rgb.clear();throw;}
}
}
