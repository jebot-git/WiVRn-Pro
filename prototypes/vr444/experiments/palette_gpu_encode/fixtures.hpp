// SPDX-License-Identifier: MIT
#pragma once
#include "encoder.hpp"
namespace palette_fixtures {
using namespace palette_encode;
struct Fixture { std::string name;unsigned width,height;std::array<std::vector<uint32_t>,2> pixels;std::array<rgb_palette::Bytes,2> reference; };
inline void add_generated(std::vector<Fixture>& fixtures,unsigned w,unsigned h,unsigned kind,unsigned q) {
    Fixture f;f.name=std::to_string(w)+"x"+std::to_string(h)+"-pattern"+std::to_string(kind)+"-q"+std::to_string(q);f.width=w;f.height=h;
    for(unsigned eye=0;eye<2;++eye) {
        auto& p=f.pixels[eye];p.resize(size_t(w)*h);uint32_t seed=0x712ab89+eye;
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x) {
            seed=seed*1664525u+1013904223u;unsigned value=0;
            if(kind==0)value=eye*0x25678+0x194866;
            if(kind==1)value=seed>>8;
            if(kind>=2&&kind<=4) { auto n=kind==2?2u:kind==3?4u:16u;value=((x+3*y+eye)%n)*0x0f1331; }
            if(kind==5)value=((x%16+(y%16)*16)/7+eye)*0x052913;
            if(kind==6)value=(x*0x293567+y*0x2589+eye*0x76543)&0xffffff;
            if(kind==7)value=((x/16+y/16)%3==0)?0x29588a:(seed&0xffffff);
            p[size_t(y)*w+x]=0xff000000|(value&0xffffff);
        }
        p=vr444::decode(vr444::encode(p,w,h,q));f.reference[eye]=rgb_palette::encode(p,w,h);
    }
    fixtures.push_back(std::move(f));
}
inline std::vector<uint8_t> read(const std::filesystem::path& path) {
    auto bytes=std::filesystem::file_size(path);require(bytes&&bytes<=16*1024*1024,"fixture size");std::vector<uint8_t> out(bytes);std::ifstream in(path,std::ios::binary);in.read(reinterpret_cast<char*>(out.data()),bytes);require(bool(in),"fixture read");return out;
}
inline void corpus(std::vector<Fixture>& cases,const std::filesystem::path& path,const std::string& label) {
    std::vector<std::filesystem::path> paths;for(auto& item:std::filesystem::recursive_directory_iterator(path))if(item.is_regular_file()&&item.path().extension()==".rpt")paths.push_back(item.path());
    std::sort(paths.begin(),paths.end());require(paths.size()<=128,"corpus count");size_t total=0;
    for(auto& item:paths) {
        auto wire=read(item);auto decoded=rgb_palette::decode(wire);auto name=item;name.replace_extension(".rgba");auto bytes=read(name);total+=bytes.size()+wire.size();require(total<=128*1024*1024,"corpus bytes");
        require(bytes.size()==decoded.rgba.size()*4&&!std::memcmp(bytes.data(),decoded.rgba.data(),bytes.size()),"independent RGBA mismatch");
        auto canonical=rgb_palette::encode(decoded.rgba,decoded.width,decoded.height);
        cases.push_back({label+"/"+std::filesystem::relative(item,path).string(),decoded.width,decoded.height,{decoded.rgba,decoded.rgba},{canonical,canonical}});
    }
}
inline void validate_pair(const Fixture& f) { for(auto& eye:f.pixels)validate(eye,f.width,f.height); }
}
