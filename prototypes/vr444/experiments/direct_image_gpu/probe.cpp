// SPDX-License-Identifier: MIT
#include "gpu_oracle.hpp"
using namespace direct_image;
struct Fixture { std::string name; std::array<vr444::Frame,2> eyes; };
std::vector<Fixture> fixtures(bool full) {
    std::vector<Fixture> result;
    std::vector<std::pair<unsigned,unsigned>> dimensions{{1,1},{1,17},{17,1},{17,19},{31,33},{65,47},{256,2}};
    if(full) dimensions.emplace_back(576,640);
    for(auto [w,h]:dimensions) for(unsigned kind=0;kind<6;++kind) for(unsigned q:{1u,8u}) {
        Fixture item{std::to_string(w)+"x"+std::to_string(h)+"-pattern"+std::to_string(kind)+"-q"+std::to_string(q),{}};
        for(unsigned eye=0;eye<2;++eye) {
            std::vector<uint32_t> pixels(size_t(w)*h); uint32_t seed=0x124578u+eye;
            for(unsigned y=0;y<h;++y) for(unsigned x=0;x<w;++x) {
                seed=1664525u*seed+1013904223u;
                unsigned tile=(y/16)*((w+15)/16)+x/16;
                uint32_t p=0xff000000u|((tile*0x152b49u+eye*0x156u)&0xffffffu);
                if(kind==1) p=0xff000000u|((seed>>8)&0xffffffu);
                if(kind==2) p=0xff000000u|(((x+y+eye)&1)?0xffffffu:0);
                if(kind==3) p=0xff000000u|((x+eye)&255)|(((x*5+y)&255)<<8)|(((x+y*7)&255)<<16);
                if(kind==4) p=0xff000000u|(((x+eye*3)%23<2||y%31<2)?0x20f080u:0x8020f0u);
                if(kind==5&&tile%2) p=0xff000000u|(seed&0xffffffu);
                pixels[size_t(y)*w+x]=p;
            }
            std::vector<uint32_t> qs;
            if(kind==5) { qs.resize(((w+15)/16)*((h+15)/16)); for(size_t i=0;i<qs.size();++i) qs[i]=std::array<unsigned,4>{1,8,17,1024}[i%4]; }
            item.eyes[eye]=vr444::encode(pixels,w,h,q,qs);
        }
        result.push_back(std::move(item));
    }
    return result;
}
std::vector<uint8_t> read_file(const std::filesystem::path& path) {
    auto bytes=std::filesystem::file_size(path); require(bytes<=16*1024*1024,"corpus file bound");
    std::vector<uint8_t> data(bytes); std::ifstream file(path,std::ios::binary); file.read(reinterpret_cast<char*>(data.data()),bytes); require(bool(file),"corpus read"); return data;
}
int main(int argc,char** argv) { try {
    require(argc>=2,"usage: direct-image SHADERS [--device N] [--reps N] [--full] [--corpus DIR] [--format-list] [--allow-software] [--cpu-only]");
    unsigned selected=0,reps=1; bool full=false,software=false,format_list=false,cpu_only=false; std::filesystem::path corpus;
    for(int i=2;i<argc;++i) {
        std::string arg=argv[i];
        if(arg=="--full") full=true;
        else if(arg=="--cpu-only") cpu_only=true;
        else if(arg=="--allow-software") software=true;
        else if(arg=="--format-list") format_list=true;
        else { require(i+1<argc,"option value missing"); std::string value=argv[++i];
            if(arg=="--device"||arg=="--reps") { size_t consumed=0; auto n=std::stoul(value,&consumed); require(consumed==value.size()&&n<=100,"numeric option bound"); if(arg=="--device") selected=n; else reps=n; }
            else if(arg=="--corpus") corpus=value;
            else throw std::runtime_error("unknown option "+arg);
        }
    }
    require(reps>=1&&reps<=30,"repetition count");
    auto cases=fixtures(full);
    if(!corpus.empty()) {
        std::vector<std::filesystem::path> paths;
        for(auto& e:std::filesystem::recursive_directory_iterator(corpus)) if(e.is_regular_file()&&e.path().extension()==".payload") paths.push_back(e.path());
        std::sort(paths.begin(),paths.end()); require(paths.size()<=128,"corpus count bound"); size_t total=0,expanded=0;
        for(auto& path:paths) { auto data=read_file(path); total+=data.size(); require(total<=64*1024*1024,"total corpus size bound"); auto f=vr444::unpack(data); expanded+=f.slots.size()*sizeof(uint32_t)*2; require(expanded<=256*1024*1024,"expanded corpus memory bound"); cases.push_back({"corpus/"+std::filesystem::relative(path,corpus).string(),{f,f}}); }
    }
    if(cpu_only) {
        extension_gate_test();
        for(const auto& fixture:cases) for(const auto& f:fixture.eyes) {
            auto roundtrip=vr444::unpack(vr444::pack(f));
            require(vr444::decode(roundtrip)==vr444::decode(f),"fixture wire roundtrip");
            auto malformed=f; malformed.lengths.back()=vr444::SLOT_WORDS+1;
            bool rejected=false; try { vr444::validate(malformed); } catch(const std::exception&) { rejected=true; }
            require(rejected,"invalid tile length accepted");
        }
        std::cout<<"{\"schema\":1,\"cpu_fixture_pass\":true,\"fixture_count\":"<<cases.size()<<",\"extension_gate_pass\":true,\"vulkan_initialized\":false,\"headset_used\":false}\n";
        return 0;
    }
    std::map<std::pair<unsigned,unsigned>,std::unique_ptr<Session>> sessions;
    std::cout<<"{\"schema\":1,\"experiment\":\"direct-image-gpu\",\"headset_used\":false,\"format_list\":"<<(format_list?"true":"false")<<",\"warmups\":1,\"repetitions\":"<<reps<<",\"cases\":["<<std::flush;
    unsigned index=0;
    for(auto& fixture:cases) {
        auto key=std::make_pair(fixture.eyes[0].width,fixture.eyes[0].height);
        if(!sessions[key]) sessions[key]=std::make_unique<Session>(key.first,key.second,argv[1],selected,software,format_list);
        auto& session=*sessions[key];
        std::array<std::vector<uint32_t>,2> expected{vr444::decode(fixture.eyes[0]),vr444::decode(fixture.eyes[1])};
        std::array<std::vector<float>,2> baseline_samples;
        // A malformed right eye must reject before poisoning/writing/submitting
        // either eye. Count submissions to distinguish fail-closed admission.
        auto malformed=fixture.eyes; malformed[1].lengths.back()=vr444::SLOT_WORDS+1;
        unsigned before=session.submissions; bool rejected=false;
        try { session.run(malformed,0,expected); } catch(const std::exception&) { rejected=true; }
        require(rejected&&session.submissions==before,"malformed pair reached GPU submission");
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(fixture.name)<<",\"device\":"<<quote(session.properties.deviceName)<<",\"device_type\":"<<session.properties.deviceType<<",\"driver\":"<<session.properties.driverVersion<<",\"input_memory_type\":"<<session.o.inputs[0][0].type<<",\"input_memory_flags\":"<<session.o.inputs[0][0].flags<<",\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<reps+1;++rep) for(unsigned step=0;step<modes;++step) {
            unsigned mode=rep==0?step:(index+rep+step)%modes;
            auto m=session.run(fixture.eyes,mode,expected);
            for(unsigned eye=0;eye<2;++eye) {
                auto* samples=static_cast<float*>(session.o.linear[eye].mapped);
                if(rep==0&&mode==0) baseline_samples[eye].assign(samples,samples+size_t(key.first)*key.second*4);
                else require(std::memcmp(samples,baseline_samples[eye].data(),session.o.linear[eye].bytes)==0,"sampled float bits differ from normal SRGB baseline");
            }
            if(rep==0) continue;
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-1<<",\"order\":"<<step<<",\"mode\":"<<quote(names[mode])<<",\"validation_ms\":"<<m.validation_ms<<",\"input_copy_ms\":"<<m.input_copy_ms<<",\"stereo_gpu_ms\":"<<m.gpu_ms<<",\"pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"oracle_ms\":"<<m.oracle_ms<<",\"max_linear_error\":"<<m.max_linear_error<<",\"max_srgb_code_error\":"<<m.max_srgb_code_error<<",\"bytes_exact\":true,\"one_eotf\":true,\"sampled_baseline_exact\":true,\"malformed_pair_rejected_before_submit\":true}";
            first=false;
        }
        std::cout<<"]}"<<std::flush; ++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"promotion_blocked\":true,\"scope\":\"Private queue standalone shader/image correctness; poison and oracle alter cache state; no XR, network, compositor or optical latency.\"}\n";
    return 0;
} catch(const std::exception& e) { std::cerr<<"direct-image: "<<e.what()<<'\n'; return 1; } }
