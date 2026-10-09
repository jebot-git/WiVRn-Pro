// SPDX-License-Identifier: MIT
#include "codec.hpp"
#include "vulkan_runner.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <memory>
#include <stdexcept>
using Clock=std::chrono::steady_clock;
struct Sample {std::string name;vr444::Frame frame;};
static std::string quote(const std::string& s){std::string out="\"";for(char c:s){if(c=='"'||c=='\\')out+='\\';out+=c;}return out+'"';}
static void dist(const char* name,std::vector<double> v){std::sort(v.begin(),v.end());std::cout<<",\""<<name<<"\":{\"p50\":"<<v[(v.size()-1)/2]<<",\"p95\":"<<v[(v.size()*95+99)/100-1]<<",\"max\":"<<v.back()<<",\"samples\":[";for(size_t i=0;i<v.size();++i)std::cout<<(i?",":"")<<v[i];std::cout<<"]}";}
static bool constant(const vr444::Frame& f,size_t i){auto p=f.slots.data()+i*vr444::SLOT_WORDS;return f.lengths[i]==3 && !(p[0]&~0xf80007ffu) && !(p[1]&~0xffc0003fu) && !(p[2]&~1u);}
int main(int argc,char** argv){try{
 if(argc<4||argc>6)throw std::runtime_error("usage: probe BASE_SHADERS FAST_SHADERS CORPUS [DEVICE=0] [REPS=21]");
 unsigned device=argc>4?std::stoul(argv[4]):0,reps=argc>5?std::stoul(argv[5]):21;if(!reps||reps>1000)throw std::runtime_error("repetitions 1..1000");
 std::vector<Sample> samples;std::vector<std::filesystem::path> paths;
 for(auto& e:std::filesystem::recursive_directory_iterator(argv[3]))if(e.is_regular_file()&&e.path().extension()==".payload")paths.push_back(e.path());
 std::sort(paths.begin(),paths.end());for(const auto& path:paths){auto size=std::filesystem::file_size(path);if(size>16*1024*1024)throw std::runtime_error("bounded corpus file");std::vector<uint8_t> data(size);std::ifstream file(path,std::ios::binary);file.read(reinterpret_cast<char*>(data.data()),size);if(!file)throw std::runtime_error("read corpus");samples.push_back({std::filesystem::relative(path,argv[3]).string(),vr444::unpack(data)});}
 const std::pair<unsigned,unsigned> dims[]={{1,1},{1,17},{17,1},{15,33},{17,19},{31,31},{32,32},{65,47},{576,640}};
 for(auto [w,h]:dims)for(unsigned kind=0;kind<4;++kind){std::vector<uint32_t> pixels(size_t(w)*h);unsigned seed=1234567;for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x){seed=1664525u*seed+1013904223u;unsigned tile=(y/16)*((w+15)/16)+x/16;uint32_t p=0xff000000u|((tile*0x152b49u)&0xffffffu);if(kind==1 && tile%2)p=0xff000000u|(seed&0xffffffu);if(kind==2 && x%16==3 && y%16==4)p^=1u;if(kind==3)p=0xff000000u|(((x+y)&1)?0xffffffu:0);pixels[size_t(y)*w+x]=p;}
 std::vector<uint32_t> map(((w+15)/16)*((h+15)/16));for(size_t i=0;i<map.size();++i)map[i]=std::array<unsigned,4>{1,8,17,1024}[i%4];
 samples.push_back({"edge/"+std::to_string(w)+"x"+std::to_string(h)+"-kind"+std::to_string(kind),vr444::encode(pixels,w,h,1,map)});}
 // A minimum nonzero repeated AC group adds only seven bits to the
 // 81-bit constant record, so length==3 alone is not a safe shortcut.
 for(unsigned active=0;active<48;++active){auto f=vr444::encode(std::vector<uint32_t>(256,0xff808080u),16,16,1);std::fill(f.slots.begin(),f.slots.end(),0);unsigned bit=0;auto put=[&](unsigned value,unsigned n){for(unsigned i=0;i<n;++i,++bit)f.slots[bit/32]|=((value>>i)&1u)<<(bit%32);};for(unsigned c=0;c<3;++c){put(c==0?256:0,11);for(unsigned group=0;group<16;++group){bool on=c*16+group==active;put(on,1);if(on){put(1,4);put(0,2);put(1,1);}}}f.lengths[0]=(bit+31)/32;vr444::validate(f);if(constant(f,0))throw std::runtime_error("constant detector false positive");samples.push_back({"edge/three-word-nonconstant-ac"+std::to_string(active),std::move(f)});}
 {auto f=vr444::encode(std::vector<uint32_t>(256,0xff808080u),16,16,1);f.lengths[0]=4;bool rejected=false;try{vr444::validate(f);}catch(const std::exception&){rejected=true;}if(!rejected)throw std::runtime_error("trailing tile padding accepted");}
 if(samples.empty()||samples.size()>10000)throw std::runtime_error("corpus count");
 using Sessions=std::pair<std::unique_ptr<vr444::GpuSession>,std::unique_ptr<vr444::GpuSession>>;std::map<std::pair<unsigned,unsigned>,Sessions> sessions;
 std::cout<<"{\"schema\":1,\"kernel\":\"constant-direct-coop128\",\"repetitions\":"<<reps<<",\"warmups\":3,\"cases\":["<<std::flush;
 size_t ci=0;for(auto& sample:samples){auto& f=sample.frame;auto key=std::make_pair(f.width,f.height);auto& pair=sessions[key];if(!pair.first){pair.first=std::make_unique<vr444::GpuSession>(f.width,f.height,argv[1],"coop128",device);pair.second=std::make_unique<vr444::GpuSession>(f.width,f.height,argv[2],"coop128",device);}auto oracle=vr444::decode(f);std::vector<double> bg,fg,bw,fw;std::string name;unsigned hits=0;for(size_t t=0;t<f.lengths.size();++t)hits+=constant(f,t);
 for(unsigned rep=0;rep<reps+3;++rep)for(unsigned which=0;which<2;++which){bool fast=(which+(rep&1))%2;auto start=Clock::now();auto result=(fast?pair.second:pair.first)->decode(f);double wall=std::chrono::duration<double,std::milli>(Clock::now()-start).count();if(result.software)throw std::runtime_error("hardware required");name=result.device;if(result.rgba!=oracle){auto p=std::mismatch(result.rgba.begin(),result.rgba.end(),oracle.begin()).first-result.rgba.begin();throw std::runtime_error(sample.name+" mismatch "+(fast?"fast":"base")+" rep="+std::to_string(rep)+" pixel="+std::to_string(p)+" got="+std::to_string(result.rgba[p])+" expected="+std::to_string(oracle[p]));}if(rep>=3){(fast?fg:bg).push_back(result.decode_ms);(fast?fw:bw).push_back(wall);}}
 std::cout<<(ci++?",":"")<<"{\"name\":"<<quote(sample.name)<<",\"device\":"<<quote(name)<<",\"width\":"<<f.width<<",\"height\":"<<f.height<<",\"q\":"<<f.q<<",\"tiles\":"<<f.lengths.size()<<",\"constant_tiles\":"<<hits<<",\"exact\":true";dist("base_gpu_ms",bg);dist("fast_gpu_ms",fg);dist("base_wall_ms",bw);dist("fast_wall_ms",fw);std::cout<<"}"<<std::flush;
 }std::cout<<"],\"pass\":true}\n";
}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
