// SPDX-License-Identifier: MIT
#include "fixtures.hpp"
using namespace rowfilter_gpu;
int main(int argc,char** argv) {try {
    require(argc>=3,"usage: rowfilter-gpu SHADERS_ROOT GOLDEN_DIR [--columns 1|4|16] [--corpus DIR] [--full] [--max-size] [--device N] [--format-list] [--cpu-only]");
    bool full=false,maximum=false,format_list=false,cpu_only=false;unsigned columns=4,selected=0;std::filesystem::path corpus;
    for(int i=3;i<argc;++i) {
        std::string arg=argv[i];
        if(arg=="--full")full=true;else if(arg=="--max-size")maximum=true;
        else if(arg=="--format-list")format_list=true;else if(arg=="--cpu-only")cpu_only=true;
        else {require(i+1<argc,"missing option");std::string value=argv[++i];
            if(arg=="--corpus")corpus=value;
            else {size_t end=0;auto n=std::stoul(value,&end);require(end==value.size()&&n<=16,"numeric option bound");if(arg=="--columns")columns=n;else if(arg=="--device")selected=n;else throw std::runtime_error("unknown option "+arg);}
        }
    }
    require(columns==1||columns==4||columns==16,"column layout");extension_gate_test();
    auto cases=generated(full,maximum);append_corpus(cases,argv[2],"golden");if(!corpus.empty())append_corpus(cases,corpus,"corpus");
    std::array<unsigned,3> counts{};
    for(auto& fixture:cases)for(unsigned eye=0;eye<2;++eye) {
        auto shape=rowfilter::inspect(fixture.wire[eye]);++counts[shape.mode];
        std::vector<uint32_t> decoded(fixture.expected[eye].size());rowfilter::Workspace workspace;
        rowfilter::decode_into(fixture.wire[eye],decoded,workspace);require(decoded==fixture.expected[eye],"CPU fixture mismatch");
        auto bad=fixture.wire[eye];bad.back()^=1;bool rejected=false;
        try{rowfilter::unpack_rgb(bad,workspace);}catch(const std::exception&){rejected=true;}
        require(rejected&&workspace.rgb.empty(),"failed unpack retained output");
    }
    for(auto count:counts)require(count>0,"all RRF modes required");
    if(cpu_only){std::cout<<"{\"cpu_fixture_pass\":true,\"fixture_count\":"<<cases.size()<<",\"rrf_modes\":3,\"vulkan_initialized\":false,\"headset_used\":false}\n";return 0;}
    auto shader_path=std::filesystem::path(argv[1])/("shaders-"+std::to_string(columns));
    std::unique_ptr<Session> session;
    std::cout<<"{\"schema\":1,\"experiment\":\"rowfilter-gpu\",\"headset_used\":false,\"columns\":"<<columns<<",\"format_list\":"<<(format_list?"true":"false")<<",\"cpu_prefix_restore_in_path\":false,\"cpu_grammar_crc_lz4_in_path\":true,\"rrf_modes\":3,\"rrf_eye_mode_counts\":["<<counts[0]<<","<<counts[1]<<","<<counts[2]<<"],\"warmups\":1,\"repetitions\":1,\"cases\":["<<std::flush;
    unsigned index=0;
    for(const auto& fixture:cases) {
        if(!session||session->width!=fixture.width||session->height!=fixture.height)
            session=std::make_unique<Session>(fixture.width,fixture.height,shader_path,selected,false,format_list);
        auto& s=*session;require(s.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU||s.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,"hardware GPU required");
        std::array<std::vector<float>,2> baseline;
        auto bad=fixture;bad.wire[1].back()^=1;unsigned before=s.submissions;bool rejected=false;
        std::memset(s.o.inputs[0][0].mapped,0xcd,64);
        try{run_rowfilter(s,bad,0,columns);}catch(const std::exception&){rejected=true;}
        auto* sentinel=static_cast<uint8_t*>(s.o.inputs[0][0].mapped);
        require(rejected&&s.submissions==before&&std::all_of(sentinel,sentinel+64,[](auto b){return b==0xcd;}),"malformed right eye reached writes/submission");
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(fixture.name)<<",\"device\":"<<quote(s.properties.deviceName)<<",\"device_type\":"<<s.properties.deviceType<<",\"driver\":"<<s.properties.driverVersion<<",\"input_memory_flags\":"<<s.o.inputs[0][0].flags<<",\"width\":"<<fixture.width<<",\"height\":"<<fixture.height<<",\"dispatch_groups\":"<<(fixture.width+columns-1)/columns<<",\"stereo_raw_rgb_bytes\":"<<uint64_t(fixture.width)*fixture.height*6<<",\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<2;++rep)for(unsigned order=0;order<modes;++order) {
            unsigned mode=rep?(index+order)%modes:order;auto m=run_rowfilter(s,fixture,mode,columns);
            for(unsigned eye=0;eye<2;++eye){auto* samples=static_cast<float*>(s.o.linear[eye].mapped);
                if(!rep&&!mode)baseline[eye].assign(samples,samples+size_t(fixture.width)*fixture.height*4);
                else require(!std::memcmp(samples,baseline[eye].data(),s.o.linear[eye].bytes),"RRF sampled values differ from same-device baseline");}
            if(!rep)continue;
            std::cout<<(first?"":",")<<"{\"mode\":"<<quote(names[mode])<<",\"validation_lz4_ms\":"<<m.validation_ms<<",\"input_copy_ms\":"<<m.input_copy_ms<<",\"stereo_gpu_ms\":"<<m.gpu_ms<<",\"pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"oracle_ms\":"<<m.oracle_ms<<",\"max_linear_error\":"<<m.max_linear_error<<",\"max_srgb_code_error\":"<<m.max_srgb_code_error<<",\"bytes_exact\":true,\"one_eotf\":true,\"sampled_baseline_exact\":true,\"malformed_pair_rejected_before_write_or_submit\":true}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"promotion_blocked\":true,\"scope\":\"Complete CPU CRC/grammar/LZ4 admission plus GPU RGB prefix/expansion; host correctness only, no XR or performance promotion\"}\n";return 0;
}catch(const std::exception& e){std::cerr<<"rowfilter-gpu: "<<e.what()<<'\n';return 1;}}
