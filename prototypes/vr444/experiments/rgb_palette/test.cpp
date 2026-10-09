// SPDX-License-Identifier: MIT
#include "rgb_palette.hpp"
#include "codec.hpp"
#include "wpc.hpp"
#include <array>
#include <cstring>
#include <iostream>
#include <random>
#include <stdexcept>
using namespace rgb_palette;
static size_t roundtrips=0,rejections=0,mutations=0;
static void need(bool v) { if(!v)throw std::runtime_error("test failed"); }
static void set32(Bytes& p,size_t at,uint32_t v){for(int i=0;i<4;++i)p[at+i]=uint8_t(v>>(8*i));}
static void repair(Bytes& p) {set32(p,24,wpc::crc(std::span(p).subspan(32)));set32(p,28,wpc::crc(std::span(p).first(28)));}
template<class F> static void reject(F fn) { bool failed=false;try{fn();}catch(const std::runtime_error&){failed=true;}need(failed);++rejections; }
static void reject_wire(const Bytes& p,size_t pixels=256) {
    reject([&]{decode(p);});std::vector<uint32_t> out(pixels,0x12345678u);
    reject([&]{decode_into(p,out);});for(auto c:out)need(c==0x12345678u);
}
static Bytes trip(std::vector<uint32_t> p,unsigned w,unsigned h) {
    auto wire=encode(p,w,h);auto out=decode(wire);need(out.width==w&&out.height==h&&out.rgba==p);
    std::vector<uint32_t> into(p.size(),0x12345678u);decode_into(wire,into);need(into==p);++roundtrips;return wire;
}
int main(){try{
    std::mt19937 rng(90210);std::array<size_t,6> mode_counts{};
    for(auto [w,h]:std::array<std::pair<unsigned,unsigned>,10>{{{1,1},{1,17},{17,1},{15,33},{17,19},{31,31},{32,32},{65,47},{576,640},{4096,1}}}) {
        for(unsigned kind=0;kind<7;++kind) {
            std::vector<uint32_t> p(size_t(w)*h);
            for(size_t i=0;i<p.size();++i) {
                unsigned color=kind==0?0:kind==1?i%2:kind==2?i%3:kind==3?i%16:kind==4?(i/19)%17:kind==5?rng():i;
                p[i]=0xff000000u|(kind==5?color&0xffffffu:(color*0x153759u)&0xffffffu);
            }
            trip(p,w,h);Stats s;encode(p,w,h,&s);
            std::array<unsigned,6> counts{s.raw,s.constant,s.palette2,s.palette4,s.palette16,s.runs};
            for(size_t i=0;i<6;++i)mode_counts[i]+=counts[i];
            if(w<=65)for(unsigned q:{1u,8u,1024u}) {
                auto f=vr444::encode(p,w,h,q);auto existing=vr444::decode(f);trip(existing,w,h);
            }
        }
    }
    for(auto n:mode_counts)need(n>0);
    std::vector<uint32_t> small(17*19);for(auto& c:small)c=0xff000000u|(rng()&0xffffffu);
    auto p=trip(small,17,19);for(size_t n=0;n<p.size();++n)reject_wire(Bytes(p.begin(),p.begin()+n),small.size());
    auto bad=p;bad.push_back(0);reject_wire(bad,small.size());
    for(unsigned i=0;i<10000;++i) {bad=p;bad[rng()%bad.size()]^=uint8_t(1u<<(rng()%8));reject_wire(bad,small.size());++mutations;}
    // Recompute both CRCs so malformed tile grammar is actually reached.
    auto base=encode(std::vector<uint32_t>(256,0xff010203u),16,16);
    for(auto [at,value]:std::array<std::pair<size_t,uint8_t>,9>{{{32,0},{32,4},{33,255},{34,6},{35,1},{6,8},{4,2},{8,0},{16,0}}}) {
        bad=base;bad[at]=value;repair(bad);reject_wire(bad);
    }
    std::vector<uint32_t> palette(256);for(size_t i=0;i<palette.size();++i)palette[i]=0xff000000u|uint32_t(i%3);
    auto pal=encode(palette,16,16);need(pal[34]==3);
    bad=pal;bad[36]=5;repair(bad);reject_wire(bad);
    bad=pal;bad[46]=255;repair(bad);reject_wire(bad); // index 3 is outside a three-color table
    bad=pal;std::copy_n(bad.begin()+37,3,bad.begin()+40);repair(bad);reject_wire(bad);
    auto tail=encode(std::span(palette).first(15),15,1);need(tail[34]==3);
    bad=tail;bad.back()|=0xc0;repair(bad);reject_wire(bad,15);
    std::vector<uint32_t> runs(256);for(unsigned i=0;i<256;++i)runs[i]=0xff000000u|(i/17);
    auto run=encode(runs,16,16);need(run[34]==5);
    bad=run;bad[36]=255;repair(bad);reject_wire(bad);
    bad=run;std::copy_n(bad.begin()+37,3,bad.begin()+41);repair(bad);reject_wire(bad);
    // CRC-valid parser mutations reach grammar checks, rather than only CRC rejection.
    for(unsigned i=0;i<3000;++i) {
        const auto& source=i%3==0?pal:i%3==1?run:base;
        bad=source;bad[32+rng()%(bad.size()-32)]^=uint8_t(1u<<(rng()%8));repair(bad);
        std::vector<uint32_t> pixels(256,0x12345678u);bool valid=true;
        try{decode_into(bad,pixels);}catch(const std::runtime_error&){valid=false;}
        if(valid)need(decode(bad).rgba==pixels);
        else {for(auto c:pixels)need(c==0x12345678u);reject([&]{decode(bad);});}
        ++mutations;
    }
    trip(std::vector<uint32_t>(max_pixels,0xff102030u),4096,1024);
    reject([&]{encode(std::vector<uint32_t>{0},1,1);});
    reject([&]{encode({},0,1);});reject([&]{encode({},4097,1);});reject([&]{encode({},4096,4096);});
    std::vector<uint32_t> out(255,0x12345678u);reject([&]{decode_into(base,out);});for(auto c:out)need(c==0x12345678u);
    std::vector<uint32_t> aliased(256);std::memcpy(aliased.data(),base.data(),base.size());
    reject([&]{decode_into(std::span(reinterpret_cast<const uint8_t*>(aliased.data()),base.size()),aliased);});
    std::cout<<"{\"pass\":true,\"exact_roundtrips\":"<<roundtrips<<",\"rejections\":"<<rejections<<",\"mutations\":"<<mutations<<",\"all_six_modes\":true,\"maximum_pixels_checked\":"<<max_pixels<<"}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
