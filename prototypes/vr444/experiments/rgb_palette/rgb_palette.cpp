// SPDX-License-Identifier: MIT
#include "rgb_palette.hpp"
#include "wpc.hpp"
#include <algorithm>
#include <array>
#include <limits>
#include <stdexcept>

namespace rgb_palette {
namespace {
constexpr uint32_t magic = 0x31545052; // RPT1
constexpr size_t header_bytes = 32;
void need(bool v) { if (!v) throw std::runtime_error("invalid RPT1 image"); }
uint16_t u16(std::span<const uint8_t> p, size_t at) {
    need(at <= p.size() && p.size()-at >= 2);
    return uint16_t(p[at]) | uint16_t(p[at+1]) << 8;
}
uint32_t u32(std::span<const uint8_t> p, size_t at) {
    need(at <= p.size() && p.size()-at >= 4);
    return uint32_t(p[at]) | uint32_t(p[at+1]) << 8 |
           uint32_t(p[at+2]) << 16 | uint32_t(p[at+3]) << 24;
}
void put16(Bytes& p, uint16_t v) { p.push_back(uint8_t(v)); p.push_back(uint8_t(v>>8)); }
void put32(Bytes& p, uint32_t v) { for (int i=0; i<4; ++i) p.push_back(uint8_t(v>>(8*i))); }
void set32(Bytes& p, size_t at, uint32_t v) { for(int i=0;i<4;++i)p[at+i]=uint8_t(v>>(8*i)); }
uint32_t rgb(std::span<const uint8_t> p, size_t at) {
    need(at <= p.size() && p.size()-at >= 3);
    return uint32_t(p[at]) | uint32_t(p[at+1])<<8 | uint32_t(p[at+2])<<16 | 0xff000000u;
}
void putrgb(Bytes& p,uint32_t v) { for(int i=0;i<3;++i)p.push_back(uint8_t(v>>(8*i))); }
size_t checked_pixels(uint32_t w,uint32_t h) {
    need(w>0 && h>0 && w<=max_axis && h<=max_axis && uint64_t(w)*h<=max_pixels);
    return size_t(w)*h;
}
uint32_t count_tiles(uint32_t w,uint32_t h) { return ((w+15)/16)*((h+15)/16); }
struct Header { uint32_t w,h,count; };
struct Tile { size_t at; uint16_t bytes; uint8_t mode; uint32_t x,y,w,h; };
struct Parsed { Header header; std::vector<Tile> tiles; };

// Work is bounded by dimensions, then record length, then <=256 pixels per tile.
// No coefficient parsing, transform, frame reference, recursion or dictionary.
Parsed preflight(std::span<const uint8_t> wire) {
    need(wire.size()>=header_bytes && wire.size()<=max_wire_bytes);
    need(u32(wire,0)==magic && u16(wire,4)==1 && u16(wire,6)==tile_edge);
    Header h{u32(wire,8),u32(wire,12),u32(wire,16)};
    checked_pixels(h.w,h.h);
    need(h.count==count_tiles(h.w,h.h) && u32(wire,20)==wire.size()-header_bytes);
    need(wpc::crc(wire.first(28))==u32(wire,28));
    need(wpc::crc(wire.subspan(header_bytes))==u32(wire,24));
    // The index is at most 65,536 records; output is not allocated yet.
    Parsed result{h,{}}; result.tiles.reserve(h.count);
    size_t at=header_bytes;
    for(uint32_t y=0;y<h.h;y+=tile_edge) for(uint32_t x=0;x<h.w;x+=tile_edge) {
        need(at<=wire.size() && wire.size()-at>=4);
        auto bytes=u16(wire,at); auto mode=wire[at+2]; need(wire[at+3]==0);
        at+=4; need(bytes>0 && bytes<=768 && bytes<=wire.size()-at && mode<=5);
        uint32_t tw=std::min(tile_edge,h.w-x),th=std::min(tile_edge,h.h-y),n=tw*th;
        auto p=wire.subspan(at,bytes);
        if(mode==0) need(bytes==3*n);
        else if(mode==1) need(bytes==3);
        else if(mode>=2 && mode<=4) {
            unsigned bits=1u<<(mode-2), colors=p[0], max_colors=1u<<bits;
            unsigned minimum_colors=mode==2?2:mode==3?3:5;
            need(colors>=minimum_colors && colors<=max_colors);
            size_t packed=(size_t(n)*bits+7)/8, start=1+3*colors;
            need(bytes==start+packed);
            for(unsigned i=0;i<colors;++i)for(unsigned j=0;j<i;++j)
                need(rgb(p,1+3*i)!=rgb(p,1+3*j));
            for(unsigned i=0;i<n;++i)
                need(((p[start+(i*bits)/8]>>((i*bits)%8))&(max_colors-1))<colors);
            unsigned tail=(n*bits)%8;
            if(tail)need((p.back()>>tail)==0);
        } else {
            need(bytes%4==0); unsigned covered=0; uint32_t previous=0;
            for(size_t i=0;i<bytes;i+=4) {
                unsigned run=unsigned(p[i])+1; uint32_t color=rgb(p,i+1);
                need(covered+run<=n && (i==0 || color!=previous));
                covered+=run; previous=color;
            }
            need(covered==n);
        }
        result.tiles.push_back({at,bytes,mode,x,y,tw,th}); at+=bytes;
    }
    need(at==wire.size() && result.tiles.size()==h.count);
    return result;
}

void expand(std::span<const uint8_t> wire,const Parsed& parsed,std::span<uint32_t> output) {
    for(auto& t:parsed.tiles) {
        auto p=wire.subspan(t.at,t.bytes); unsigned n=t.w*t.h;
        if(t.mode==1) {
            const uint32_t color=rgb(p,0);
            for(unsigned y=0;y<t.h;++y)
                std::fill_n(output.begin()+size_t(t.y+y)*parsed.header.w+t.x,t.w,color);
            continue;
        }
        std::array<uint32_t,256> pixels;
        if(t.mode==0)for(unsigned i=0;i<n;++i)pixels[i]=rgb(p,3*i);
        else if(t.mode>=2 && t.mode<=4) {
            std::array<uint32_t,16> palette{}; unsigned colors=p[0],bits=1u<<(t.mode-2);
            for(unsigned i=0;i<colors;++i)palette[i]=rgb(p,1+3*i);
            size_t start=1+3*colors; unsigned mask=(1u<<bits)-1;
            for(unsigned i=0;i<n;++i)pixels[i]=palette[(p[start+(i*bits)/8]>>((i*bits)%8))&mask];
        } else {
            unsigned out=0;
            for(size_t i=0;i<p.size();i+=4) {
                unsigned run=unsigned(p[i])+1;
                std::fill_n(pixels.begin()+out,run,rgb(p,i+1)); out+=run;
            }
        }
        for(unsigned y=0;y<t.h;++y)
            std::copy_n(pixels.begin()+y*t.w,t.w,output.begin()+size_t(t.y+y)*parsed.header.w+t.x);
    }
}
}

Bytes encode(std::span<const uint32_t> rgba,uint32_t w,uint32_t h,Stats* stats) {
    need(rgba.size()==checked_pixels(w,h));
    for(auto c:rgba)need((c>>24)==255);
    Stats s; Bytes wire; wire.reserve(header_bytes+3*rgba.size()+4*count_tiles(w,h));
    put32(wire,magic);put16(wire,1);put16(wire,tile_edge);put32(wire,w);put32(wire,h);
    put32(wire,count_tiles(w,h));put32(wire,0);put32(wire,0);put32(wire,0);
    for(uint32_t y=0;y<h;y+=tile_edge)for(uint32_t x=0;x<w;x+=tile_edge) {
        unsigned tw=std::min(tile_edge,w-x),th=std::min(tile_edge,h-y),n=tw*th;
        std::array<uint32_t,256> pixels; unsigned pos=0;
        for(unsigned ty=0;ty<th;++ty)for(unsigned tx=0;tx<tw;++tx)pixels[pos++]=rgba[size_t(y+ty)*w+x+tx];
        // A constant tile needs neither candidate buffers nor palette/run scans.
        if(std::all_of(pixels.begin()+1,pixels.begin()+n,[&](uint32_t v){return v==pixels[0];})) {
            put16(wire,3);wire.push_back(1);wire.push_back(0);putrgb(wire,pixels[0]);++s.constant;
            continue;
        }
        Bytes best;best.reserve(3*n);for(unsigned i=0;i<n;++i)putrgb(best,pixels[i]);
        uint8_t mode=0; std::array<uint32_t,16> palette{};std::array<uint8_t,256> indexes{};unsigned colors=0;
        for(unsigned i=0;i<n;++i) {
            unsigned j=0;while(j<colors && palette[j]!=pixels[i])++j;
            if(j==colors) { if(colors==16){colors=17;break;} palette[colors++]=pixels[i]; }
            indexes[i]=uint8_t(j);
        }
        if(colors==1) { best.clear();putrgb(best,palette[0]);mode=1; }
        else if(colors<=16) {
            unsigned bits=colors<=2?1:colors<=4?2:4; Bytes p; p.push_back(uint8_t(colors));
            for(unsigned i=0;i<colors;++i)putrgb(p,palette[i]);size_t start=p.size();p.resize(start+(n*bits+7)/8,0);
            for(unsigned i=0;i<n;++i)p[start+(i*bits)/8]|=uint8_t(indexes[i]<<((i*bits)%8));
            if(p.size()<best.size()){best=std::move(p);mode=bits==1?2:bits==2?3:4;}
        }
        Bytes runs; for(unsigned i=0;i<n;) {
            unsigned end=i+1;while(end<n && pixels[end]==pixels[i])++end;
            runs.push_back(uint8_t(end-i-1));putrgb(runs,pixels[i]);i=end;
        }
        if(runs.size()<best.size()){best=std::move(runs);mode=5;}
        switch(mode){case 0:++s.raw;break;case 1:++s.constant;break;case 2:++s.palette2;break;
            case 3:++s.palette4;break;case 4:++s.palette16;break;case 5:++s.runs;break;}
        put16(wire,uint16_t(best.size()));wire.push_back(mode);wire.push_back(0);wire.insert(wire.end(),best.begin(),best.end());
    }
    set32(wire,20,uint32_t(wire.size()-header_bytes));set32(wire,24,wpc::crc(std::span(wire).subspan(header_bytes)));
    set32(wire,28,wpc::crc(std::span(wire).first(28)));if(stats)*stats=s;return wire;
}

Image decode(std::span<const uint8_t> wire) {
    auto parsed=preflight(wire);
    Image out{parsed.header.w,parsed.header.h,std::vector<uint32_t>(size_t(parsed.header.w)*parsed.header.h)};
    expand(wire,parsed,out.rgba);return out;
}
void decode_into(std::span<const uint8_t> wire,std::span<uint32_t> output) {
    auto a=reinterpret_cast<uintptr_t>(wire.data()),b=reinterpret_cast<uintptr_t>(output.data());
    // Subtraction avoids pointer/end overflow and comparison of unrelated pointers.
    need(wire.empty() || output.empty() || (a<=b?b-a>=wire.size():a-b>=output.size_bytes()));
    auto parsed=preflight(wire);need(output.size()==size_t(parsed.header.w)*parsed.header.h);
    expand(wire,parsed,output);
}
}
