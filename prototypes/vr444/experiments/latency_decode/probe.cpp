// SPDX-License-Identifier: MIT
#include "direct.hpp"
#include "codec.hpp"
#include "vulkan_runner.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstring>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <random>
#include <stdexcept>
using Clock=std::chrono::steady_clock;
static double ms(Clock::time_point t){return std::chrono::duration<double,std::milli>(Clock::now()-t).count();}
static void require(bool ok,const char* text){if(!ok)throw std::runtime_error(text);}
static std::vector<uint8_t> read(const std::filesystem::path& p){size_t n=std::filesystem::file_size(p);require(n<=16*1024*1024,"file bound");std::vector<uint8_t> d(n);std::ifstream f(p,std::ios::binary);f.read(reinterpret_cast<char*>(d.data()),n);require(bool(f),"file read");return d;}
static std::string quote(const std::string& s){std::string r="\"";for(char c:s){if(c=='\\'||c=='\"')r+='\\';r+=c;}return r+'\"';}
static void distribution(const char* key,std::vector<double> values){std::sort(values.begin(),values.end());std::cout<<",\""<<key<<"\":{\"p50\":"<<values[(values.size()-1)/2]<<",\"p95\":"<<values[(values.size()*95+99)/100-1]<<",\"max\":"<<values.back()<<",\"samples\":[";for(size_t i=0;i<values.size();++i)std::cout<<(i?",":"")<<values[i];std::cout<<"]}";}
struct Sample {std::string name;std::vector<uint8_t> wire;};
static std::vector<Sample> generated(){
 std::vector<Sample> samples;const std::pair<unsigned,unsigned> dims[]={{1,1},{1,17},{17,1},{15,33},{17,19},{31,31},{32,32},{65,47},{576,640}};
 for(auto [w,h]:dims)for(unsigned kind=0;kind<4;++kind){std::vector<uint32_t> pixels(size_t(w)*h);unsigned seed=1234567;for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){seed=1664525u*seed+1013904223u;unsigned tile=(y/16)*((w+15)/16)+x/16;uint32_t p=0xff000000u|((tile*0x152b49u)&0xffffffu);if(kind==1&&tile%2)p=0xff000000u|(seed&0xffffffu);if(kind==2&&x%16==3&&y%16==4)p^=1u;if(kind==3)p=0xff000000u|(((x+y)&1)?0xffffffu:0);pixels[size_t(y)*w+x]=p;}
 std::vector<uint32_t> map(((w+15)/16)*((h+15)/16));for(size_t i=0;i<map.size();++i)map[i]=std::array<unsigned,4>{1,8,17,1024}[i%4];samples.push_back({"edge/"+std::to_string(w)+"x"+std::to_string(h)+"-kind"+std::to_string(kind),vr444::pack(vr444::encode(pixels,w,h,1,map))});}
 for(unsigned active=0;active<48;++active){auto f=vr444::encode(std::vector<uint32_t>(256,0xff808080u),16,16,1);std::fill(f.slots.begin(),f.slots.end(),0);unsigned bit=0;auto put=[&](unsigned value,unsigned n){for(unsigned i=0;i<n;++i,++bit)f.slots[bit/32]|=((value>>i)&1u)<<(bit%32);};for(unsigned c=0;c<3;++c){put(c==0?256:0,11);for(unsigned group=0;group<16;++group){bool on=c*16+group==active;put(on,1);if(on){put(1,4);put(0,2);put(1,1);}}}f.lengths[0]=(bit+31)/32;vr444::validate(f);samples.push_back({"edge/three-word-nonconstant-ac"+std::to_string(active),vr444::pack(f)});}
 return samples;
}
static int self_test(){
 size_t checked=0,rejected=0,accepted=0;auto compare=[&](const std::vector<uint8_t>& wire){std::vector<uint32_t> oracle;bool ref_ok=true;try{oracle=vr444::decode(vr444::unpack(wire));}catch(const std::runtime_error&){ref_ok=false;}
  for(bool simd:{false,true}){bool ok=true;std::vector<uint32_t> out;try{auto shape=direct::preflight(wire);out.resize(size_t(shape.width)*shape.height,0xdeadbeef);direct::decode(wire,shape,out,simd);}catch(const std::runtime_error&){ok=false;}require(ok==ref_ok,"parser acceptance differs");if(ok){require(out==oracle,"CPU oracle differs");++accepted;}else ++rejected;++checked;}};
 for(auto& s:generated())compare(s.wire);
 auto small=vr444::pack(vr444::encode(vr444::scene(17,19,2),17,19,8,{1,8,17,1024}));for(size_t n=0;n<small.size();++n)compare({small.begin(),small.begin()+n});auto trailing=small;trailing.push_back(0);compare(trailing);
 std::mt19937 rng(371);for(unsigned i=0;i<10000;++i){auto bad=small;bad[rng()%bad.size()]^=1u<<(rng()%8);compare(bad);}
 for(unsigned color=0;color<4096;++color){uint32_t rgb=0xff000000u|((color&15)*17)|(((color>>4&15)*17)<<8)|(((color>>8&15)*17)<<16);compare(vr444::pack(vr444::encode({rgb},1,1,1)));}
 // Explicit input/output alias and incorrect shape/output-span checks.
 auto f=vr444::pack(vr444::encode({0xff336699u},1,1,1));auto sh=direct::preflight(f);std::vector<uint32_t> mem((f.size()+3)/4);std::memcpy(mem.data(),f.data(),f.size());bool bad=false;try{direct::decode(std::span(reinterpret_cast<const uint8_t*>(mem.data()),f.size()),sh,std::span(mem.data(),1),true);}catch(const std::runtime_error&){bad=true;}require(bad,"alias accepted");++checked;++rejected;
 std::vector<uint32_t> out(1);bad=false;auto wrong=sh;++wrong.width;try{direct::decode(f,wrong,out,true);}catch(const std::runtime_error&){bad=true;}require(bad,"wrong shape accepted");++checked;++rejected;
 std::cout<<"{\"pass\":true,\"vector_backend\":"<<quote(direct::vector_backend())<<",\"checks\":"<<checked<<",\"accepted_exact\":"<<accepted<<",\"rejected\":"<<rejected<<"}\n";return 0;
}
int main(int argc,char** argv){try{
 if(argc==2&&std::string(argv[1])=="--self-test")return self_test();
 if(argc<3||argc>4)throw std::runtime_error("usage: probe CORPUS REPS [COOP128DC_SHADERS]; or --self-test");unsigned reps=std::stoul(argv[2]);require(reps>=1&&reps<=200,"repetition bound");std::vector<Sample> samples;std::vector<std::filesystem::path> paths;for(auto& e:std::filesystem::recursive_directory_iterator(argv[1]))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());std::sort(paths.begin(),paths.end());require(paths.size()<=512,"file count bound");size_t total=0;for(const auto& p:paths){auto data=read(p);total+=data.size();require(total<=64*1024*1024,"corpus bytes bound");samples.push_back({std::filesystem::relative(p,argv[1]).string(),std::move(data)});}auto extra=generated();samples.insert(samples.end(),std::make_move_iterator(extra.begin()),std::make_move_iterator(extra.end()));
 std::map<std::pair<unsigned,unsigned>,std::unique_ptr<vr444::GpuSession>> gpu;std::cout<<"{\"schema\":1,\"vector_backend\":"<<quote(direct::vector_backend())<<",\"warmups\":3,\"repetitions\":"<<reps<<",\"gpu_enabled\":"<<(argc==4?"true":"false")<<",\"cases\":["<<std::flush;size_t ci=0;
 for(auto& sample:samples){auto shape=direct::preflight(sample.wire);auto oracle=vr444::decode(vr444::unpack(sample.wire));std::vector<uint32_t> scalar(oracle.size()),vector(oracle.size());direct::Stats stats{};auto key=std::make_pair(shape.width,shape.height);if(argc==4&&!gpu[key])gpu[key]=std::make_unique<vr444::GpuSession>(shape.width,shape.height,argv[3],"coop128dc",0);
  std::array<std::vector<double>,6> timing; // reference CPU, direct scalar, direct SIMD, GPU total, GPU dispatch, preflight
  for(unsigned rep=0;rep<reps+3;++rep){for(unsigned j=0;j<3;++j){unsigned which=(j+rep)%3;double elapsed;auto start=Clock::now();if(which==0){auto ref=vr444::decode(vr444::unpack(sample.wire));elapsed=ms(start);require(ref==oracle,"reference unstable");}else{auto& pixels=which==1?scalar:vector;std::fill(pixels.begin(),pixels.end(),0x5a5a5a5a);start=Clock::now();stats=direct::decode(sample.wire,shape,pixels,which==2);elapsed=ms(start);if(pixels!=oracle)throw std::runtime_error(sample.name+" direct mismatch variant"+std::to_string(which));}if(rep>=3)timing[which].push_back(elapsed);}
   auto start=Clock::now();auto s=direct::preflight(sample.wire);auto pf=ms(start);require(s.tiles==shape.tiles,"preflight changed");if(rep>=3)timing[5].push_back(pf);
   if(argc==4){start=Clock::now();auto expanded=vr444::unpack(sample.wire);auto result=gpu[key]->decode(expanded);auto elapsed=ms(start);require(!result.software&&result.rgba==oracle,"GPU oracle failure");if(rep>=3){timing[3].push_back(elapsed);timing[4].push_back(result.decode_ms);}}
  }
  std::cout<<(ci++?",":"")<<"{\"name\":"<<quote(sample.name)<<",\"width\":"<<shape.width<<",\"height\":"<<shape.height<<",\"q\":"<<shape.q<<",\"wire_bytes\":"<<sample.wire.size()<<",\"expanded_slot_bytes\":"<<size_t(shape.tiles)*1280<<",\"tiles\":"<<shape.tiles<<",\"constant_tiles\":"<<stats.constant_tiles<<",\"mixed_tiles\":"<<stats.mixed_tiles<<",\"rgba_bytes\":"<<oracle.size()*4<<",\"exact\":true";distribution("reference_cpu_total_ms",timing[0]);distribution("direct_scalar_ms",timing[1]);distribution("direct_simd_ms",timing[2]);distribution("preflight_only_ms",timing[5]);if(argc==4){distribution("gpu_total_with_unpack_ms",timing[3]);distribution("gpu_dispatch_ms",timing[4]);}std::cout<<"}"<<std::flush;
 }std::cout<<"],\"pass\":true}\n";
 }catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
