// SPDX-License-Identifier: MIT
#include "../palette_gpu_encode/fixtures.hpp"
using namespace palette_encode;
using namespace palette_fixtures;
constexpr std::array<OutputMode,3> arms{OutputMode::full_copy,OutputMode::mapped,OutputMode::compact_copy};
void add_ties(std::vector<Fixture>& cases) {
    std::vector<std::vector<unsigned>> colors{{0},{0,1,2,3,0},{0,0,1},{}};
    for(unsigned i=0;i<18;++i) { colors[3].push_back(i);if(i<6)colors[3].push_back(i); }
    const std::array<unsigned,4> widths{1,5,3,8},heights{1,1,1,3},expected_modes{1,0,2,0};
    for(unsigned test=0;test<4;++test) {
        Fixture f;f.name="canonical-tie-"+std::to_string(test);f.width=widths[test];f.height=heights[test];
        for(unsigned eye=0;eye<2;++eye) {
            for(auto c:colors[test])f.pixels[eye].push_back(0xff000000|((c*0x13527u+eye*0x35129u)&0xffffff));
            f.reference[eye]=rgb_palette::encode(f.pixels[eye],f.width,f.height);
            require(rgb_palette::inspect(f.reference[eye]).tiles[0].mode==expected_modes[test],"independent tie expectation");
        }
        cases.push_back(std::move(f));
    }
}
int main(int argc,char** argv) { try {
    require(argc>=3,"usage: palette-output SHADERS GOLDEN_DIR [--corpus DIR] [--extra-goldens DIR] [--full] [--max-size] [--cpu-only] [--device N] [--reps N]");
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
    corpus(cases,argv[2],"golden");if(!extra.empty())corpus(cases,extra,"extra-golden");if(!capture.empty())corpus(cases,capture,"captured");add_ties(cases);
    std::array<size_t,6> modes{};
    for(auto& f:cases) {
        validate_pair(f);for(unsigned eye=0;eye<2;++eye) { require(rgb_palette::decode(f.reference[eye]).rgba==f.pixels[eye],"CPU reference changed pixels");for(auto t:rgb_palette::inspect(f.reference[eye]).tiles)++modes[t.mode]; }
        auto bad=f;bad.pixels[1].back()&=0xffffff;bool rejected=false;try { validate_pair(bad); }catch(const std::exception&) { rejected=true; }require(rejected,"invalid right alpha accepted");
    }
    for(auto n:modes)require(n,"encoder mode coverage");
    if(cpu) { std::cout<<"{\"cpu_fixture_pass\":true,\"fixture_count\":"<<cases.size()<<",\"canonical_modes\":6,\"canonical_tie_cases\":4,\"vulkan_initialized\":false,\"headset_used\":false}\n";return 0; }
    std::map<std::pair<unsigned,unsigned>,std::array<std::unique_ptr<Encoder>,3>> sessions;
    std::cout<<"{\"schema\":1,\"experiment\":\"palette-output-memory\",\"headset_used\":false,\"performance_claim\":false,\"promotion_blocked\":true,\"warmups\":1,\"repetitions\":"<<reps<<",\"canonical_modes\":6,\"canonical_tie_cases\":4,\"cases\":["<<std::flush;
    unsigned index=0;
    for(const auto& f:cases) {
        auto key=std::make_pair(f.width,f.height);auto& group=sessions[key];
        for(unsigned mode=0;mode<3;++mode)if(!group[mode])group[mode]=std::make_unique<Encoder>(f.width,f.height,argv[1],device,arms[mode]);
        auto& base=*group[0];
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(f.name)<<",\"device\":"<<quote(base.properties.deviceName)<<",\"driver\":"<<base.properties.driverVersion<<",\"device_type\":"<<base.properties.deviceType<<",\"width\":"<<f.width<<",\"height\":"<<f.height<<",\"tiles\":"<<base.tiles<<",\"scan_blocks\":"<<base.blocks<<",\"stereo_uploaded_bytes\":"<<size_t(f.width)*f.height*8<<",\"stereo_readback_capacity_bytes\":"<<base.o.raw[0].bytes*2<<",\"eye_wire_bytes\":["<<f.reference[0].size()<<","<<f.reference[1].size()<<"],\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<=reps;++rep)for(unsigned order=0;order<3;++order) {
            unsigned mode=(index+rep+order)%3;auto& e=*group[mode];auto bad=f;bad.pixels[1].back()&=0xffffff;auto before=e.submissions;bool rejected=false;
            try { validate_pair(bad); }catch(const std::exception&) { rejected=true; }require(rejected&&e.submissions==before,"malformed pair admitted");
            auto start=Clock::now();validate_pair(f);double preflight=elapsed(start);std::array<Result,2> result{e.run(f.pixels[0]),e.run(f.pixels[1])};
            for(unsigned eye=0;eye<2;++eye) { require(result[eye].wire==f.reference[eye],"GPU output-memory arm differs from canonical CPU bytes: "+f.name);require(rgb_palette::decode(result[eye].wire).rgba==f.pixels[eye],"GPU output-memory arm changed pixels"); }
            if(!rep)continue;
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-1<<",\"order\":"<<order<<",\"mode\":"<<quote(mode_name(arms[mode]))<<",\"input_memory_flags\":"<<e.buffer(0).flags<<",\"output_memory_flags\":"<<e.buffer(5).flags<<",\"readback_memory_flags\":"<<e.o.raw[0].flags<<",\"stereo_copied_bytes\":"<<result[0].copied_bytes+result[1].copied_bytes<<",\"queue_submissions\":"<<result[0].queue_submissions+result[1].queue_submissions<<",\"stereo_preflight_ms\":"<<preflight+result[0].validation_ms+result[1].validation_ms<<",\"stereo_upload_ms\":"<<result[0].upload_ms+result[1].upload_ms<<",\"stereo_device_timestamp_span_ms\":"<<result[0].gpu_copy_ms+result[1].gpu_copy_ms<<",\"stereo_submit_wait_ms\":"<<result[0].submit_wait_ms+result[1].submit_wait_ms<<",\"stereo_assembly_crc_ms\":"<<result[0].assembly_crc_ms+result[1].assembly_crc_ms<<",\"canonical_bytes_exact\":true,\"decoded_pixels_exact\":true,\"malformed_pair_rejected_before_submit\":true}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Offline output-memory arms; timestamp span includes compact-arm CPU dependency; no integration, network, XR or performance promotion\"}\n";return 0;
}catch(const std::exception& e) { std::cerr<<"palette-output: "<<e.what()<<'\n';return 1; } }
