// SPDX-License-Identifier: MIT
#include "codec_test/vr444/vulkan_runner.hpp"
#include <algorithm>
#include <chrono>
#include <iostream>
#include <stdexcept>
#include <array>
using clock_type=std::chrono::steady_clock;
double elapsed(clock_type::time_point t){return std::chrono::duration<double,std::milli>(clock_type::now()-t).count();}
void require(bool ok){if(!ok)throw std::runtime_error("GPU/CPU reference mismatch");}
void stats(const std::vector<double>& input){auto a=input;std::sort(a.begin(),a.end());std::cout<<"{\"p50\":"<<a[a.size()/2]<<",\"p95\":"<<a[std::min(a.size()-1,size_t(a.size()*.95))]<<",\"max\":"<<a.back()<<"}";}
int main(int argc,char** argv){
 try{
  if(argc!=2)throw std::runtime_error("pass shader directory");
  vr444::self_test();std::cout<<"{\"schema\":1,\"scope\":\"standalone persistent compute, CPU copies; not display latency\",\"tests\":[";
  bool first=true;
  for(auto kernel:{"baseline","coop64","coop128"}){
   auto began=clock_type::now();vr444::GpuSession encoder(576,640,argv[1],kernel),decoder(576,640,argv[1],kernel);double init=elapsed(began);
   std::vector<double> ew,dw,eg,dg;std::string device;
   for(unsigned i=0;i<30;i++){
    unsigned q=i%2?8:1;auto rgb=vr444::scene(576,640,i%3);
    std::vector<uint32_t> map;if(i%5==0){map.resize(36*40,q);for(unsigned t=0;t<map.size();t++)if(t%3==0)map[t]=1;}
    began=clock_type::now();auto encoded=encoder.encode(rgb,q,map);ew.push_back(elapsed(began));eg.push_back(encoded.encode_ms);device=encoded.device;
    auto unpacked=vr444::unpack(vr444::pack(encoded.frame));
    began=clock_type::now();auto decoded=decoder.decode(unpacked);dw.push_back(elapsed(began));dg.push_back(decoded.decode_ms);
    require(!encoded.software&&!decoded.software);require(decoded.rgba==vr444::decode(unpacked));
    if(q==1)require(decoded.rgba==rgb);
    if(i<6)require(vr444::pack(encoded.frame)==vr444::pack(vr444::encode(rgb,576,640,q,map)));
    if(i==0){auto bad=unpacked;bad.slots[0]=2047;bool rejected=false;try{decoder.decode(bad);}catch(const std::exception&){rejected=true;}require(rejected);require(decoder.decode(unpacked).rgba==decoded.rgba);}
   }
   if(!first)std::cout<<",";first=false;
   std::cout<<"{\"kernel\":\""<<kernel<<"\",\"device\":\""<<device<<"\",\"frames\":30,\"reference_exact\":true,\"two_contexts_init_ms\":"<<init<<",\"encode_wall_ms\":";stats(ew);std::cout<<",\"decode_wall_ms\":";stats(dw);std::cout<<",\"encode_gpu_ms\":";stats(eg);std::cout<<",\"decode_gpu_ms\":";stats(dg);std::cout<<"}"<<std::flush;
  }std::cout<<"]}\n";
 }catch(const std::exception& e){std::cerr<<e.what()<<"\n";return 1;}
}
