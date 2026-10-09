// SPDX-License-Identifier: MIT
#include "codec.hpp"
#include <algorithm>
#include <chrono>
#include <cmath>
#include <filesystem>
#include <fstream>
#include <iomanip>
#include <iostream>
#include <stdexcept>

static std::vector<uint32_t> read(const std::string& p,size_t expected){
    if(std::filesystem::file_size(p)!=expected*4)throw std::runtime_error("input size");
    std::vector<uint32_t> v(expected);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(v.data()),v.size()*4);
    if(!f)throw std::runtime_error("input read");return v;
}
struct Metric{double sum=0;uint64_t n=0,errors=0;unsigned max=0;};
static void emit(const Metric& m){
    std::cout<<"{\"rgb_samples\":"<<m.n<<",\"nonzero_error_samples\":"<<m.errors<<",\"max_abs_rgb_error\":"<<m.max<<",\"psnr_db\":";
    if(!m.n||!m.sum)std::cout<<"null";else std::cout<<10*std::log10(65025.*m.n/m.sum);
    std::cout<<"}";
}
int main(int argc,char** argv){try{
    if(argc!=8)throw std::runtime_error("usage: probe WIDTH HEIGHT RGBA QMAP MASK UNIFORM_Q_OR_ZERO WIRE_OUT");
    unsigned w=std::stoul(argv[1]),h=std::stoul(argv[2]),uniform=std::stoul(argv[6]);
    if(!w||!h||w>4096||h>4096||uint64_t(w)*h>4194304||uniform>1024)throw std::runtime_error("bounds");
    auto image=read(argv[3],size_t(w)*h);size_t count=((w+15)/16)*((h+15)/16);
    auto q=read(argv[4],count),mask=read(argv[5],count);
    for(size_t i=0;i<count;++i)if(q[i]<1||q[i]>1024||mask[i]>1)throw std::runtime_error("map bounds");
    if(!uniform&&std::all_of(q.begin(),q.end(),[&](auto v){return v==q.front();}))uniform=q.front();
    auto encode=[&]{return vr444::pack(vr444::encode(image,w,h,uniform?uniform:1,uniform?std::vector<uint32_t>{}:q));};
    auto wire=encode();auto decoded=vr444::decode(vr444::unpack(wire));
    {std::ofstream f(argv[7],std::ios::binary);f.write(reinterpret_cast<const char*>(wire.data()),wire.size());if(!f)throw std::runtime_error("wire write");}
    Metric metrics[3];uint64_t lossless_tile_errors=0;
    for(size_t i=0;i<image.size();++i){auto tile=(i/w/16)*((w+15)/16)+(i%w/16);
        for(unsigned c=0;c<3;++c){unsigned d=std::abs(int((image[i]>>(8*c))&255)-int((decoded[i]>>(8*c))&255));
            for(unsigned region:{0u,mask[tile]?1u:2u}){auto& m=metrics[region];m.sum+=d*d;m.n++;m.errors+=d!=0;m.max=std::max(m.max,d);}
            if((uniform?uniform:q[tile])==1&&d)++lossless_tile_errors;
        }
    }
    if(lossless_tile_errors)throw std::runtime_error("Q1 tile not exact");
    std::vector<double> times;volatile size_t sink=0;
    for(unsigned i=0;i<5;++i){auto start=std::chrono::steady_clock::now();auto v=encode();auto stop=std::chrono::steady_clock::now();sink=v.size();times.push_back(std::chrono::duration<double,std::milli>(stop-start).count());}
    (void)sink;std::sort(times.begin(),times.end());
    std::cout<<std::setprecision(10)<<"{\"bytes\":"<<wire.size()<<",\"serialized_q_map_bytes\":"<<(uniform?0:count*2)<<",\"encode_pack_ms_median\":"<<times[2]<<",\"encode_pack_ms_max\":"<<times.back()<<",\"q1_tile_rgb_errors\":"<<lossless_tile_errors<<",\"regions\":{\"whole\":";emit(metrics[0]);std::cout<<",\"guarded_fovea\":";emit(metrics[1]);std::cout<<",\"periphery\":";emit(metrics[2]);std::cout<<"}}\n";
}catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}}
