// No Vulkan/OpenXR/device calls; exercise the actual bounded native recorder.
#include "source_mapping.h"
#include <fstream>
#include <iterator>
#include <iostream>
#include <filesystem>
using namespace wivrn::codec_test::source_mapping;
static void check(bool value){if(!value)throw std::runtime_error("native mapping assertion");}
static Record valid(){
 Record r;r.compositor=9;r.frame=90;r.warp=8;r.semaphore_value=1;r.layer_target_ns=1;r.submit_return_host_ns=2;r.complete_host_ns=3;
 r.width=r.height=5;r.direct_projection=true;r.layer_count=1;
 for(unsigned i=0;i<2;++i){auto&e=r.eyes[i];e.swapchain=1+i;e.image=3+i;e.image_view=5+i;e.source_width=e.source_height=5;
  e.source_array_size=e.source_face_count=e.source_sample_count=e.source_mip_count=1;e.source_image_count=3;
  e.source_format=e.view_format=43;e.rect={0,0,5,5};e.fov_bits_lrud={std::bit_cast<uint32_t>(-.5f),std::bit_cast<uint32_t>(.5f),std::bit_cast<uint32_t>(.5f),std::bit_cast<uint32_t>(-.5f)};
  e.x_params_count=e.y_params_count=1;e.x_params[0]=e.y_params[0]=5;for(unsigned k=0;k<6;++k)e.x_edges[k]=e.y_edges[k]=k;
 }return r;
}
static std::string read(const std::filesystem::path&p){std::ifstream f(p);return {std::istreambuf_iterator<char>(f),{}};}
int main(int argc,char**argv){try{
 check(argc==2);const std::filesystem::path dir=argv[1];check(std::filesystem::create_directory(dir));unsigned checks=0;
 auto r=valid();check(supported(r));++checks;
 auto rejected=[&](auto mutate){auto bad=r;mutate(bad);check(!supported(bad));++checks;};
 rejected([](auto&b){b.width=4097;});rejected([](auto&b){b.width=4096;b.height=1025;});
 rejected([](auto&b){b.eyes[1].source_width=0;});rejected([](auto&b){b.eyes[1].array_index=1;});
 rejected([](auto&b){b.eyes[0].image_index=3;});rejected([](auto&b){b.eyes[0].source_sample_count=2;});
 rejected([](auto&b){b.eyes[0].rect[2]=-5;});rejected([](auto&b){b.eyes[0].x_edges[3]=0;});
 rejected([](auto&b){b.eyes[0].y_edges[2]=10;});rejected([](auto&b){b.view_space=true;});
 rejected([](auto&b){b.frame=91;});rejected([](auto&b){b.complete_host_ns=1;});
 {Trace t((dir/"valid.jsonl").c_str());{Completion guard(t);t.append(r);guard.complete();}r.eyes[0].x_edges[1]=99;t.flush();}
 check(read(dir/"valid.jsonl").find("\"x_edges\":[0,1,2,3,4,5]")!=std::string::npos);++checks;
 check(read(dir/"valid.jsonl").find("\"complete\":true")!=std::string::npos);++checks;r=valid();
 for(const auto*kind:{"timeout","throw"}){
  Trace t((dir/(std::string(kind)+".jsonl")).c_str());try{Completion guard(t);if(std::string(kind)=="throw")throw std::runtime_error("GPU submit/present/wait failure");}catch(const std::runtime_error&){}
  t.flush();check(read(dir/(std::string(kind)+".jsonl")).find("\"failed\":1,\"complete\":false")!=std::string::npos);++checks;
 }
 {Trace t((dir/"overflow.jsonl").c_str());for(unsigned n=0;n<capacity+1;++n)t.append(r);t.flush();}
 check(read(dir/"overflow.jsonl").find("\"dropped\":1,\"failed\":0,\"complete\":false")!=std::string::npos);++checks;
 bool failed=false;try{Trace t((dir/"forbidden.jsonl").c_str(),false);}catch(const std::runtime_error&){failed=true;}
 check(failed&&!std::filesystem::exists(dir/"forbidden.jsonl"));++checks;
 {Trace t(nullptr,false);{Completion guard(t);}check(!t.enabled()&&!t.sample(90));++checks;}
 std::cout<<"{\"status\":\"pass\",\"checks\":"<<checks<<",\"GPU_exercised\":false}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
