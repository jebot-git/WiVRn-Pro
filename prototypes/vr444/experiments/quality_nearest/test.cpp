#include "codec_test/quality_sampler.h"
#include <array>
#include <iostream>
using namespace wivrn::codec_test::quality_sampler;
int main(int argc,char** argv){
    if(argc!=2)return 2;unsigned checks=0;
    auto check=[&](bool good){if(!good)throw std::runtime_error("sampler admission regression");++checks;};
    auto reject=[&](auto call){bool failed=false;try{call();}catch(const std::runtime_error&){failed=true;}check(failed);};
    check(parse(std::nullopt)==Mode::linear);check(parse("off")==Mode::linear);check(parse("q1-nearest")==Mode::nearest);
    for(auto token:{"","nearest","linear","q8-nearest","q1-nearest "," q1-nearest","q1-nearest\n","OFF"})reject([&]{parse(token);});
    for(bool captured:{false,true})for(bool paired:{false,true})for(auto payload:{"rrb32","vr444"})for(auto compression:{"none","lz4"}){
        check(client(Mode::linear,captured,payload,compression,paired)==Mode::linear);
        if(captured&&paired&&std::string_view(payload)=="rrb32"&&std::string_view(compression)=="none")check(client(Mode::nearest,captured,payload,compression,paired)==Mode::nearest);
        else reject([&]{client(Mode::nearest,captured,payload,compression,paired);});
    }
    check(server("off",false,"","","","","")==Mode::linear);
    check(server("q1-nearest",true,"rrb32-q1","1","none","1","/tmp/source")==Mode::nearest);
    reject([&]{server("q1-nearest",false,"rrb32-q1","1","none","1","/tmp/source");});
    std::array<std::string,5> options{"rrb32-q1","1","none","1","/tmp/source"};
    for(unsigned i=0;i<5;++i){auto bad=options;bad[i]="invalid";reject([&]{server("q1-nearest",true,bad[0],bad[1],bad[2],bad[3],bad[4]);});}
    std::filesystem::path path=argv[1];check(!read(path));
    auto put=[&](std::string value){std::ofstream(path,std::ios::binary).write(value.data(),value.size());};
    for(auto text:{"off","q1-nearest","q1-nearest\n","q1-nearest\r\n"}){put(text);auto token=read(path);check(parse(*token)==(std::string_view(text)=="off"?Mode::linear:Mode::nearest));}
    for(auto text:{std::string(),std::string("q1-nearest\n\n"),std::string("q1-nearest\0x",12),std::string(64,'a'),std::string(65,'a'),std::string(4096,'a')}){put(text);reject([&]{auto token=read(path);parse(*token);});}
    std::filesystem::remove(path);std::filesystem::create_directory(path);reject([&]{read(path);});std::filesystem::remove(path);
    std::filesystem::create_symlink(path.string()+"-missing",path);reject([&]{read(path);});std::filesystem::remove(path);
    std::cout<<checks<<" strict sampler admission and file-reader checks passed\n";
}
