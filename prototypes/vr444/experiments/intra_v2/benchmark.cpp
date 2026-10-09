// SPDX-License-Identifier: MIT
#include "intra_v2.hpp"
#include <algorithm>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <numeric>
#include <stdexcept>
#include <string>
using Clock=std::chrono::steady_clock;
struct Case {std::string name;std::vector<uint8_t> original;};
std::vector<uint8_t> read(const std::filesystem::path& p){std::ifstream f(p,std::ios::binary);if(!f)throw std::runtime_error("open "+p.string());return {std::istreambuf_iterator<char>(f),{}};}
std::string quoted(const std::string& s){std::string out="\"";for(char c:s){if(c=='"'||c=='\\')out+='\\';out+=c;}return out+'"';}
template<class F> std::array<double,2> time_ms(F&& f){
    f();std::array<double,5> values;
    for(auto& value:values){auto start=Clock::now();f();value=std::chrono::duration<double,std::milli>(Clock::now()-start).count();}
    std::sort(values.begin(),values.end());return {values[2],values[4]};
}
std::vector<uint32_t> synthetic(unsigned kind,unsigned w,unsigned h){
    if(kind<3)return vr444::scene(w,h,kind);
    std::vector<uint32_t> pixels(size_t(w)*h);
    for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){
        unsigned color;
        if(kind==3){ // Deterministic thin glyph-like strokes, not a font asset.
            unsigned gx=x%11,gy=y%17;bool text=y%43<17 && (gx==1 || gy==1 || gy==8 || (gx==8 && gy<9));
            color=text?0x00f4f4f4:0x001b1d21;
        }else if(kind==4){ // Diagonal edges, checker texture, and isolated thin chroma lines.
            bool edge=((x+2*y)/31)%2;color=edge?0x0027d4f1:0x00cb231a;
            if(x%73==0||y%61==0)color=0x0000ff00;
        }else color=0x005b7a99;
        pixels[size_t(y)*w+x]=color|0xff000000;
    }
    return pixels;
}
int main(int argc,char** argv){
    if(argc<2||argc>3)throw std::runtime_error("usage: benchmark WORKSPACE [--dc-only]");
    const bool adaptive=argc==2;
    if(argc==3&&std::string(argv[2])!="--dc-only")throw std::runtime_error("unknown option");
    std::filesystem::path workspace=argv[1];std::vector<Case> cases;
    for(const char* run:{"paired-a","paired-b","lossless"}){
        auto dir=workspace/"validation/results/wivrn-stereo-live-0.6.1"/run/"host";
        std::vector<std::filesystem::path> files;for(auto& file:std::filesystem::directory_iterator(dir))if(file.path().extension()==".payload")files.push_back(file.path());
        std::sort(files.begin(),files.end());for(const auto& p:files)cases.push_back({std::string(run)+"/"+p.filename().string(),read(p)});
    }
    const char* scene_names[]={"gradient","chroma-lines","seeded-noise","text-like","edges","constant"};
    for(unsigned kind=0;kind<6;++kind)for(unsigned q:{1u,8u})cases.push_back({std::string("synthetic/")+scene_names[kind]+"-q"+std::to_string(q),vr444::pack(vr444::encode(synthetic(kind,576,640),576,640,q))});
    std::cout<<std::setprecision(9)<<"{\"schema\":1,\"experiment\":"<<quoted(adaptive?"adaptive-intra-v2-coefficient-serialization":"dc-only-intra-v2-coefficient-serialization")<<",\"timing_repetitions\":5,\"cases\":[\n";
    bool first=true;
    for(const auto& item:cases){
        auto f=vr444::unpack(item.original);auto reference=vr444::decode(f);intra_v2::Stats stats;
        auto wire=intra_v2::encode(f,&stats,adaptive);auto restored=intra_v2::decode(wire);auto output=vr444::decode(restored);
        if(reference!=output)throw std::runtime_error("RGB mismatch "+item.name);
        bool restored_bitstream=vr444::pack(restored)==item.original;
        // Volatile checksum makes the timed results observable without including
        // hashing in the measured interval; bytes/pixels are checked separately.
        volatile uint64_t sink=0;
        auto old_pack=time_ms([&]{auto v=vr444::pack(f);sink=v.size()+v.back();});
        auto new_pack=time_ms([&]{auto v=intra_v2::encode(f,nullptr,adaptive);sink=v.size()+v.back();});
        auto old_unpack=time_ms([&]{auto v=vr444::unpack(item.original);sink=v.lengths.back();});
        auto new_unpack=time_ms([&]{auto v=intra_v2::decode(wire);sink=v.lengths.back();});
        auto old_decode=time_ms([&]{auto v=vr444::decode(vr444::unpack(item.original));sink=v.back();});
        auto new_decode=time_ms([&]{auto v=vr444::decode(intra_v2::decode(wire));sink=v.back();});
        (void)sink;
        if(!first)std::cout<<",\n";first=false;
        std::cout<<"{\"name\":"<<quoted(item.name)<<",\"width\":"<<f.width<<",\"height\":"<<f.height<<",\"q\":"<<f.q
                 <<",\"legacy_bytes\":"<<item.original.size()<<",\"experimental_bytes\":"<<wire.size()
                 <<",\"byte_ratio\":"<<double(wire.size())/item.original.size()<<",\"decoded_rgba_exact\":true,\"restored_legacy_bitstream_exact\":"<<(restored_bitstream?"true":"false")<<",\"tile_modes\":[";
        for(unsigned i=0;i<5;++i){if(i)std::cout<<",";std::cout<<stats.modes[i];}std::cout<<"]";
        for(auto [label,times]:{std::pair{"legacy_pack",old_pack},std::pair{"experimental_pack",new_pack},std::pair{"legacy_unpack",old_unpack},std::pair{"experimental_unpack",new_unpack},std::pair{"legacy_decode_rgb",old_decode},std::pair{"experimental_decode_rgb",new_decode}})
            std::cout<<",\""<<label<<"_ms\":{\"median\":"<<times[0]<<",\"max\":"<<times[1]<<"}";
        std::cout<<"}"<<std::flush;
    }
    std::cout<<"\n]}\n";
}
