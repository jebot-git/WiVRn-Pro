// SPDX-License-Identifier: MIT
#include "baseline/vulkan_runner.hpp"
#include "pack_mapped.hpp"
#include <algorithm>
#include <array>
#include <chrono>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <numeric>
#include <random>
#include <string>
using Clock=std::chrono::steady_clock;
using Bytes=std::vector<uint8_t>;
static double elapsed(Clock::time_point begin){return std::chrono::duration<double,std::milli>(Clock::now()-begin).count();}
static void require(bool ok,const char* what){if(!ok)throw std::runtime_error(what);}
static size_t checks=0;
static void checked(bool ok,const char* what){++checks;require(ok,what);}
template<class F> static void fails(F f){bool threw=false;try{f();}catch(const std::runtime_error&){threw=true;}checked(threw,"invalid pack accepted");}
static void self_test(){
    Bytes output;
    for(auto size:std::vector<std::pair<unsigned,unsigned>>{{1,1},{16,16},{17,19},{65,33}})
      for(unsigned kind=0;kind<3;++kind)for(unsigned q:{1u,8u,32u,128u})for(bool mapped:{false,true}){
        auto input=vr444::scene(size.first,size.second,kind);
        std::vector<uint32_t> map;
        if(mapped){map.resize(((size.first+15)/16)*((size.second+15)/16));for(size_t i=0;i<map.size();++i)map[i]=i%2?1:q;}
        auto frame=vr444::encode(input,size.first,size.second,q,map);
        latency_host::pack_mapped(frame.width,frame.height,frame.q,frame.slots,frame.lengths,frame.tile_q,output);
        checked(output==vr444::pack(frame),"CPU fixture wire differs");
        checked(vr444::decode(vr444::unpack(output))==vr444::decode(frame),"CPU fixture decode differs");
        if(q==1)checked(vr444::decode(vr444::unpack(output))==input,"q1 fixture not lossless");
    }
    auto frame=vr444::encode(vr444::scene(17,19,1),17,19,8);
    auto attempt=[&]{latency_host::pack_mapped(frame.width,frame.height,frame.q,frame.slots,frame.lengths,frame.tile_q,output);};
    const auto lengths=frame.lengths;
    for(auto value:{0u,1u,2u,321u,UINT32_MAX}){frame.lengths[0]=value;fails(attempt);}frame.lengths=lengths;
    frame.tile_q={1};fails(attempt);frame.tile_q.resize(lengths.size(),1);frame.tile_q[0]=1025;fails(attempt);frame.tile_q.clear();
    frame.q=0;fails(attempt);frame.q=8;frame.width=0;fails(attempt);frame.width=17;
    frame.slots.pop_back();fails(attempt);frame.slots.push_back(0);frame.lengths.pop_back();fails(attempt);frame.lengths=lengths;
    output.resize(65536);auto* alias=reinterpret_cast<const uint32_t*>(output.data());
    fails([&]{latency_host::pack_mapped(17,19,8,{alias,size_t(4)*vr444::SLOT_WORDS},frame.lengths,{},output);});
    std::cout<<"{\"status\":\"pass\",\"checks\":"<<checks<<"}\n";
}
struct Sample{double total_ms,input_vector_ms,input_map_ms,submit_ms,gpu_ms,expanded_or_pack_ms,finish_pack_ms,cpu_outside_submit_ms;};
static void distribution(std::ostream& out,std::vector<double> values){std::sort(values.begin(),values.end());size_t n=values.size();out<<"{\"count\":"<<n<<",\"p50\":"<<values[(n-1)/2]<<",\"p95\":"<<values[(n*95+99)/100-1]<<",\"min\":"<<values.front()<<",\"max\":"<<values.back()<<"}";}
struct Fixture{std::string name;unsigned width,height,q;std::vector<uint32_t> input,map;};
static void benchmark(const std::string& shaders,const std::filesystem::path& corpus,const std::filesystem::path& destination){
    std::vector<Fixture> fixtures;
    for(auto entry:std::filesystem::directory_iterator(corpus))if(entry.path().extension()==".rgba"){
        const auto bytes=std::filesystem::file_size(entry.path());require(bytes==size_t(576)*640*4,"invalid captured RGBA size");
        std::vector<uint32_t> input(bytes/4);std::ifstream file(entry.path(),std::ios::binary);file.read(reinterpret_cast<char*>(input.data()),bytes);require(bool(file),"capture read failure");
        fixtures.push_back({entry.path().stem().string(),576,640,8,std::move(input),{}});
    }
    std::sort(fixtures.begin(),fixtures.end(),[](const auto& a,const auto& b){return a.name<b.name;});
    require(fixtures.size()>=4,"not enough capture fixtures");
    for(unsigned kind=0;kind<3;++kind)for(unsigned q:{1u,8u})fixtures.push_back({"synthetic-"+std::to_string(kind)+"-q"+std::to_string(q),576,640,q,vr444::scene(576,640,kind),{}});
    for(unsigned q:{1u,8u}){Fixture f{"partial-q"+std::to_string(q),17,19,q,vr444::scene(17,19,1),{1,8,32,128}};fixtures.push_back(std::move(f));}
    if(std::getenv("LATENCY_HOST_REVERSE"))std::reverse(fixtures.begin(),fixtures.end());
    std::ofstream out(destination);require(bool(out),"cannot open results");
    out<<"{\"schema\":1,\"warmups\":5,\"repeats\":31,\"scope\":\"One-eye cached replay; excludes original producer readback/fence, compression, network and display. Same unchanged shaders and persistent device. Variant order rotates per repetition.\",\"cases\":[";
    bool comma=false;
    for(auto& fixture:fixtures){
        vr444::GpuSession gpu(fixture.width,fixture.height,shaders,"baseline",0);
        const auto cpu=vr444::encode(fixture.input,fixture.width,fixture.height,fixture.q,fixture.map);
        const auto cpu_wire=vr444::pack(cpu);
        const auto cpu_pixels=vr444::decode(cpu);
        auto baseline=gpu.encode(fixture.input,fixture.q,fixture.map);
        require(!baseline.software && baseline.device.find("A770")!=std::string::npos,"benchmark must run on Arc A770");
        const auto wire=vr444::pack(baseline.frame);
        checked(vr444::decode(vr444::unpack(wire))==cpu_pixels,"baseline CPU decode oracle mismatch");
        checked(wire==cpu_wire,"baseline GPU vs CPU wire mismatch");
        if(fixture.q==1 && fixture.map.empty())checked(cpu_pixels==fixture.input,"q1 GPU fixture not lossless");
        std::array<std::vector<Sample>,4> samples;
        Bytes reusable;
        std::string device=baseline.device;
        for(unsigned iteration=0;iteration<36;++iteration)for(unsigned offset=0;offset<4;++offset){
            const unsigned variant=(iteration+offset)%4;
            const auto start=Clock::now();
            std::vector<uint32_t> copied;
            if(variant!=3){copied.resize(fixture.input.size());std::memcpy(copied.data(),fixture.input.data(),copied.size()*4);}
            const double vector_ms=elapsed(start);
            auto result=variant<2?gpu.encode(copied,fixture.q,fixture.map):gpu.encode_packed(variant==2?std::span<const uint32_t>(copied):std::span<const uint32_t>(fixture.input),fixture.q,fixture.map);
            const auto pack_start=Clock::now();
            Bytes legacy;
            std::span<const uint8_t> selected;
            if(variant==0){legacy=vr444::pack(result.frame);selected=legacy;}
            else if(variant==1){latency_host::pack_mapped(fixture.width,fixture.height,fixture.q,result.frame.slots,result.frame.lengths,result.frame.tile_q,reusable);selected=reusable;}
            else selected=result.packed;
            const double pack_ms=elapsed(pack_start), total_ms=elapsed(start);
            checked(selected.size()==wire.size() && std::equal(selected.begin(),selected.end(),wire.begin()),"benchmark wire differs");
            if(iteration==0)checked(vr444::decode(vr444::unpack(Bytes(selected.begin(),selected.end())))==cpu_pixels,"alternative CPU oracle mismatch");
            if(iteration>=5)samples[variant].push_back({total_ms,vector_ms,result.input_copy_ms,result.submit_ms,result.encode_ms,result.output_copy_pack_ms,pack_ms,total_ms-result.submit_ms});
        }
        if(comma)out<<',';comma=true;
        out<<"{\"name\":\""<<fixture.name<<"\",\"width\":"<<fixture.width<<",\"height\":"<<fixture.height<<",\"q\":"<<fixture.q<<",\"mapped_q\":"<<(fixture.map.empty()?"false":"true")<<",\"device\":\""<<device<<"\",\"wire_bytes\":"<<wire.size()<<",\"variants\":[";
        const char* names[]{"baseline-vector-expanded-bytepack","vector-expanded-bulkpack","vector-mapped-bulkpack","span-mapped-bulkpack"};
        const char* labels[]{"total_ms","input_vector_ms","input_map_ms","submit_ms","gpu_ms","expanded_or_pack_ms","finish_pack_ms","cpu_outside_submit_ms"};
        for(unsigned variant=0;variant<4;++variant){if(variant)out<<',';out<<"{\"name\":\""<<names[variant]<<"\"";
            for(unsigned field=0;field<8;++field){std::vector<double> values;for(auto& s:samples[variant]){const double members[]{s.total_ms,s.input_vector_ms,s.input_map_ms,s.submit_ms,s.gpu_ms,s.expanded_or_pack_ms,s.finish_pack_ms,s.cpu_outside_submit_ms};values.push_back(members[field]);}out<<",\""<<labels[field]<<"\":";distribution(out,values);}out<<'}';}
        out<<"]}";out.flush();
        std::cout<<fixture.name<<" exact "<<wire.size()<<" bytes\n";
    }
    out<<"],\"status\":\"pass\",\"exact_checks\":"<<checks<<"}\n";
}
int main(int argc,char** argv){try{if(argc==2 && std::string(argv[1])=="--self-test")self_test();else if(argc==5 && std::string(argv[1])=="--benchmark")benchmark(argv[2],argv[3],argv[4]);else throw std::runtime_error("--self-test or --benchmark SHADERS CORPUS OUTPUT");}catch(const std::exception& e){std::cerr<<e.what()<<'\n';return 1;}}
