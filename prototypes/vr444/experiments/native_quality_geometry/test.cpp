#include "codec_test/quality_geometry.h"
#include <array>
#include <cassert>
#include <iostream>
#include <string>
using namespace wivrn::codec_test::quality_geometry;
static unsigned checks=0;
static void check(bool v){++checks;if(!v)throw std::runtime_error("check failed");}
template<class F>static void rejects(F f){++checks;try{f();}catch(const std::runtime_error&){return;}throw std::runtime_error("invalid profile accepted");}
int main(){
    check(parse(std::nullopt)==Profile{});check(parse("off")==Profile{});
    check(client(std::nullopt,false,"")==Profile{});check(client("off",false,"vr444")==Profile{});
    check(server(std::nullopt,"vulkan-vr444","8","lz4","","",576,640)==Profile{});
    check(server("off","rrb32-vr444","8","none","","",576,640)==Profile{});
    const std::array<std::string_view,3> names={"q1-768x864","q1-960x1056","q1-1152x1280"};
    const std::array<Profile,3> profiles={Profile{768,864,true},Profile{960,1056,true},Profile{1152,1280,true}};
    for(unsigned i=0;i<names.size();++i){
        const auto p=profiles[i];check(parse(names[i])==p);check(client(names[i],true,"rrb32")==p);
        check(server(names[i],"rrb32-q1","1","none","1","/owned/source.jsonl",p.width,p.height)==p);
        check(uint64_t(p.width)*p.height<=4194304&&p.width%64==0&&p.height%32==0);
        rejects([&]{client(names[i],false,"rrb32");});rejects([&]{client(names[i],true,"vr444");});
        for(const auto mode:{"","vulkan-vr444","rrb32-vr444","raw-rgba"})rejects([&]{server(names[i],mode,"1","none","1","/trace",p.width,p.height);});
        for(const auto q:{"","0","8","01","1x"})rejects([&]{server(names[i],"rrb32-q1",q,"none","1","/trace",p.width,p.height);});
        for(const auto c:{"","lz4","adaptive"})rejects([&]{server(names[i],"rrb32-q1","1",c,"1","/trace",p.width,p.height);});
        for(const auto c:{"","0","true"})rejects([&]{server(names[i],"rrb32-q1","1","none",c,"/trace",p.width,p.height);});
        for(const auto t:{"","relative.jsonl"})rejects([&]{server(names[i],"rrb32-q1","1","none","1",t,p.width,p.height);});
        rejects([&]{server(names[i],"rrb32-q1","1","none","1","/trace",576,640);});
        rejects([&]{server(std::nullopt,"rrb32-q1","1","none","1","/trace",p.width,p.height);});
    }
    for(const auto bad:{""," ","OFF","q1-576x640","q8-960x1056","960x1056","q1-4096x4096","q1-768x865","q1-960x1056\n","q1-960x1056x"})rejects([&]{parse(bad);});
    for(unsigned i=0;i<1000;++i){const auto bad="q1-"+std::to_string(i)+"x1056x";rejects([&]{parse(bad);});}
    std::cout<<checks<<" quality geometry admission/default/peer-bound checks passed\n";
}
