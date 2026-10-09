// SPDX-License-Identifier: MIT
#include "fixtures.hpp"
using namespace palette_encode;
using namespace palette_fixtures;
int main(int argc,char** argv) { try {
    require(argc>=3,"usage: palette-encode SHADERS GOLDEN_DIR [--corpus DIR] [--extra-goldens DIR] [--full] [--max-size] [--cpu-only] [--device N] [--reps N]");
    bool full=false,max_size=false,cpu=false;unsigned device=0,reps=1;std::filesystem::path capture,extra;
    for(int i=3;i<argc;++i) {
        std::string arg=argv[i];if(arg=="--full")full=true;else if(arg=="--max-size")max_size=true;else if(arg=="--cpu-only")cpu=true;
        else { require(i+1<argc,"missing argument");std::string value=argv[++i];if(arg=="--corpus")capture=value;else if(arg=="--extra-goldens")extra=value;
            else if(arg=="--device"||arg=="--reps") { size_t end=0;auto n=std::stoul(value,&end);require(end==value.size()&&n<=30,"numeric bound");if(arg=="--device")device=n;else reps=n; }else throw std::runtime_error("unknown option"); }
    }
    require(reps>=1,"repetitions");std::vector<Fixture> cases;
    std::vector<std::pair<unsigned,unsigned>> shapes{{1,1},{1,17},{17,1},{17,19},{31,33},{65,47},{256,2}};if(full)shapes.emplace_back(576,640);
    for(auto [w,h]:shapes)for(unsigned kind=0;kind<8;++kind)for(unsigned q:{1u,8u})add_generated(cases,w,h,kind,q);
    for(auto [w,h]:std::array<std::pair<unsigned,unsigned>,4>{{{4080,16},{4096,16},{17,2049},{4096,17}}})for(unsigned kind:{1u,7u})add_generated(cases,w,h,kind,1);
    if(max_size)for(unsigned kind:{0u,1u})add_generated(cases,2048,2048,kind,1);
    corpus(cases,argv[2],"golden");if(!extra.empty())corpus(cases,extra,"extra-golden");if(!capture.empty())corpus(cases,capture,"captured");
    std::array<size_t,6> modes{};
    for(auto& f:cases) {
        validate_pair(f);for(unsigned eye=0;eye<2;++eye) { require(rgb_palette::decode(f.reference[eye]).rgba==f.pixels[eye],"CPU reference changed pixels");for(auto t:rgb_palette::inspect(f.reference[eye]).tiles)++modes[t.mode]; }
        auto bad=f;bad.pixels[1].back()&=0xffffff;bool rejected=false;try { validate_pair(bad); }catch(const std::exception&) { rejected=true; }require(rejected,"invalid right alpha accepted");
    }
    for(auto n:modes)require(n,"encoder mode coverage");
    if(cpu) { std::cout<<"{\"cpu_fixture_pass\":true,\"fixture_count\":"<<cases.size()<<",\"canonical_modes\":6,\"vulkan_initialized\":false,\"headset_used\":false}\n";return 0; }
    std::map<std::pair<unsigned,unsigned>,std::unique_ptr<Encoder>> sessions;
    std::cout<<"{\"schema\":1,\"experiment\":\"palette-gpu-encode\",\"headset_used\":false,\"performance_claim\":false,\"promotion_blocked\":true,\"warmups\":1,\"repetitions\":"<<reps<<",\"canonical_modes\":6,\"cases\":["<<std::flush;
    unsigned index=0;
    for(const auto& f:cases) {
        auto key=std::make_pair(f.width,f.height);if(!sessions[key])sessions[key]=std::make_unique<Encoder>(f.width,f.height,argv[1],device);auto& e=*sessions[key];
        auto bad=f;bad.pixels[1].back()&=0xffffff;auto before=e.submissions;bool rejected=false;try { validate_pair(bad); }catch(const std::exception&) { rejected=true; }require(rejected&&e.submissions==before,"malformed pair admitted");
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(f.name)<<",\"device\":"<<quote(e.properties.deviceName)<<",\"driver\":"<<e.properties.driverVersion<<",\"device_type\":"<<e.properties.deviceType<<",\"width\":"<<f.width<<",\"height\":"<<f.height<<",\"tiles\":"<<e.tiles<<",\"scan_blocks\":"<<e.blocks<<",\"input_memory_flags\":"<<e.buffer(0).flags<<",\"output_memory_flags\":"<<e.buffer(5).flags<<",\"readback_memory_flags\":"<<e.o.raw[0].flags<<",\"stereo_uploaded_bytes\":"<<size_t(f.width)*f.height*8<<",\"stereo_readback_capacity_bytes\":"<<e.o.raw[0].bytes*2<<",\"stereo_wire_bytes\":"<<f.reference[0].size()+f.reference[1].size()<<",\"iterations\":[";
        for(unsigned rep=0;rep<=reps;++rep) {
            auto start=Clock::now();validate_pair(f);double preflight=elapsed(start);std::array<Result,2> result{e.run(f.pixels[0]),e.run(f.pixels[1])};
            for(unsigned eye=0;eye<2;++eye) { require(result[eye].wire==f.reference[eye],"GPU RPT bytes differ from canonical CPU encoder: "+f.name);require(rgb_palette::decode(result[eye].wire).rgba==f.pixels[eye],"GPU RPT decode changed pixels"); }
            if(!rep)continue;
            std::cout<<(rep==1?"":",")<<"{\"rep\":"<<rep-1<<",\"stereo_preflight_ms\":"<<preflight+result[0].validation_ms+result[1].validation_ms<<",\"stereo_upload_ms\":"<<result[0].upload_ms+result[1].upload_ms<<",\"stereo_gpu_and_full_readback_ms\":"<<result[0].gpu_copy_ms+result[1].gpu_copy_ms<<",\"stereo_submit_wait_ms\":"<<result[0].submit_wait_ms+result[1].submit_wait_ms<<",\"stereo_assembly_crc_ms\":"<<result[0].assembly_crc_ms+result[1].assembly_crc_ms<<",\"canonical_bytes_exact\":true,\"decoded_pixels_exact\":true,\"malformed_pair_rejected_before_submit\":true}";
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Offline canonical RPT1 GPU encoder; host source upload and full-capacity readback; no integration, network, XR or performance promotion\"}\n";return 0;
}catch(const std::exception& e) { std::cerr<<"palette-encode: "<<e.what()<<'\n';return 1; } }
