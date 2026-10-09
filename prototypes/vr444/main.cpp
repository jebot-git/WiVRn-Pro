// SPDX-License-Identifier: MIT
#include "codec.hpp"
#include "vulkan_runner.hpp"
#include "foveation.hpp"
#include <chrono>
#include <iostream>
#include <iomanip>
#include <algorithm>
#include <stdexcept>
#include <fstream>
#include <cstring>
using namespace vr444;
using Clock=std::chrono::steady_clock;
static double ms(Clock::time_point a){return std::chrono::duration<double,std::milli>(Clock::now()-a).count();}
static std::vector<uint8_t> read_file(const std::string& path){
    std::ifstream f(path,std::ios::binary|std::ios::ate);if(!f)throw std::runtime_error("cannot open input");
    auto size=f.tellg();if(size<0||size>64*1024*1024)throw std::runtime_error("input exceeds 64 MiB");
    std::vector<uint8_t> b(static_cast<size_t>(size));f.seekg(0);f.read(reinterpret_cast<char*>(b.data()),size);if(!f)throw std::runtime_error("input read failed");return b;
}
static void write_file(const std::string& path,const void* data,size_t size){
    std::ofstream f(path,std::ios::binary);f.write(static_cast<const char*>(data),size);if(!f)throw std::runtime_error("output write failed");
}
static void verify_gpu(unsigned w,unsigned h,unsigned kind,unsigned q,const std::string& path,bool foveated=false) {
    std::vector<uint32_t> map;
    auto a=scene(w,h,kind);
    if(foveated){
        auto prepared=prepare_foveated(scene(w*4,h*4,kind),w*4,h*4,w,h,{-45,45,-45,45},{12,0,1,100000000,true},{8,0,1,90000000,true},120000000);
        a=std::move(prepared.pixels);map=std::move(prepared.quality.q);
    }
    auto f=encode(a,w,h,q,map);auto expected=decode(f);auto result=gpu_roundtrip(a,w,h,q,path,nullptr,map);
    if(pack(result.frame)!=pack(f))throw std::runtime_error("CPU/GPU bitstream mismatch");
    if(result.rgba!=expected)throw std::runtime_error("CPU/GPU decoded pixels mismatch");
    // Exercise standalone receive/decode, including CPU bitstream input.
    if(w<100){auto received=gpu_roundtrip(a,w,h,q,path,&f);if(received.rgba!=expected)throw std::runtime_error("standalone GPU decode mismatch");}
    std::cout<<"device="<<result.device<<" software="<<result.software<<" width="<<w<<" height="<<h<<" q="<<q
        <<" foveated="<<foveated<<" encode_dispatch_ms="<<result.encode_ms<<" decode_dispatch_ms="<<result.decode_ms<<" submit_wait_ms="<<result.submit_ms<<" verified=1\n";
}
static void bench(unsigned w,unsigned h) {
    std::cout<<"scene,width,height,target_stereo_mbps,q,payload_stereo_mbps,transport_stereo_mbps,rgb_psnr_db,cpu_encode_p50_ms,cpu_decode_p50_ms,cpu_encode_p95_ms,cpu_decode_p95_ms,raw_ratio,budget_met\n";
    for(unsigned kind=0;kind<3;kind++) {
        auto a=scene(w,h,kind);
        for(unsigned target:{50u,100u,150u}) {
            Frame selected{}; double best=-1; bool met=false;
            for(unsigned q:{1u,2u,3u,4u,6u,8u,12u,16u,24u,32u,48u,64u,96u,128u,192u,256u,384u,512u,768u,1024u}) {
                auto f=encode(a,w,h,q);size_t bytes=payload_bytes(f);
                // 1168-byte payload + 32-byte application header + 28-byte IPv4/UDP.
                double wire=(bytes+((bytes+1167)/1168)*60)*2*90*8/1e6;
                if(wire<=target) {double quality=psnr(a,decode(f));if(quality>best){best=quality;selected=std::move(f);met=true;}}
            }
            if(!met){selected=encode(a,w,h,1024);best=psnr(a,decode(selected));}
            std::vector<double> enc,dec;
            for(unsigned repeat=0;repeat<7;repeat++) {
                auto start=Clock::now();auto f=encode(a,w,h,selected.q);double e=ms(start);
                start=Clock::now();auto out=decode(f);double d=ms(start);
                if(repeat){enc.push_back(e);dec.push_back(d);}
            }
            std::sort(enc.begin(),enc.end());std::sort(dec.begin(),dec.end());size_t bytes=payload_bytes(selected);
            double payload=bytes*2*90*8/1e6,transport=(bytes+((bytes+1167)/1168)*60)*2*90*8/1e6;
            std::cout<<kind<<','<<w<<','<<h<<','<<target<<','<<selected.q<<','<<payload<<','<<transport<<','<<best<<','<<enc[3]<<','<<dec[3]<<','<<enc.back()<<','<<dec.back()<<','<<double(w)*h*3/bytes<<','<<met<<'\n';
        }
    }
}
int main(int argc,char** argv) try {
    std::cout<<std::fixed<<std::setprecision(4);
    std::string mode=argc>1?argv[1]:"--self-test";
    if(mode=="--self-test"){self_test();std::cout<<"PASS: RGB444 lossless, lossy, odd dimensions, framing, all truncations, 2000 bounded mutations\n";}
    else if(mode=="--gpu-test" && argc==3){verify_gpu(17,19,2,1,argv[2]);verify_gpu(64,64,1,17,argv[2]);verify_gpu(17,19,2,1024,argv[2]);verify_gpu(64,64,2,1,argv[2],true);}
    else if(mode=="--gpu-bench" && argc==3){verify_gpu(576,640,1,16,argv[2]);}
    else if(mode=="--gpu-foveation" && argc==3){verify_gpu(576,640,1,1,argv[2],true);}
    else if(mode=="--foveation-test"){foveation_tests();}
    else if(mode=="--foveation-bench"){foveation_bench();}
    else if(mode=="--bench"){bench(576,640);}
    else if(mode=="--encode" && argc==8){
        auto bytes=read_file(argv[2]);if(bytes.size()%4)throw std::runtime_error("RGBA byte alignment");
        std::vector<uint32_t> pixels(bytes.size()/4);std::memcpy(pixels.data(),bytes.data(),bytes.size());
        auto wire=pack(encode(pixels,std::stoul(argv[3]),std::stoul(argv[4]),std::stoul(argv[5])));
        if(std::string(argv[6])!="--out")throw std::runtime_error("expected --out");
        write_file(argv[7],wire.data(),wire.size());
    }
    else if((mode=="--decode" && argc==4)||(mode=="--gpu-decode" && argc==5)){
        auto f=unpack(read_file(argv[2]));auto pixels=decode(f);
        if(mode=="--gpu-decode"){
            auto result=gpu_roundtrip(pixels,f.width,f.height,f.q,argv[3],&f);
            if(result.rgba!=pixels)throw std::runtime_error("GPU receive pixels differ from CPU reference");
            std::cout<<"device="<<result.device<<" software="<<result.software<<" decode_dispatch_ms="<<result.decode_ms<<'\n';pixels=std::move(result.rgba);
        }
        write_file(argv[argc-1],pixels.data(),pixels.size()*4);
    }
    else throw std::runtime_error("usage: vr444 --self-test | --bench | --gpu-test SHADER_DIR | --gpu-bench SHADER_DIR | --encode INPUT.rgba W H Q --out OUTPUT.vr44 | --decode INPUT.vr44 OUTPUT.rgba | --gpu-decode INPUT.vr44 SHADER_DIR OUTPUT.rgba");
    return 0;
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}
