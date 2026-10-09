// SPDX-License-Identifier: MIT
#include "fixtures.hpp"
using namespace rowblocks_gpu;
int main(int argc,char** argv) {try {
    require(argc>=3,"usage: rowblocks-gpu SHADERS_ROOT GOLDEN_DIR [--corpus DIR] [--full] [--max-size] [--device N] [--format-list] [--cpu-only]");
    bool full=false,maximum=false,format_list=false,cpu_only=false;unsigned selected=0;std::filesystem::path corpus;
    for(int i=3;i<argc;++i) {
        std::string arg=argv[i];
        if(arg=="--full")full=true;else if(arg=="--max-size")maximum=true;
        else if(arg=="--format-list")format_list=true;else if(arg=="--cpu-only")cpu_only=true;
        else {require(i+1<argc,"missing option");std::string value=argv[++i];
            if(arg=="--corpus")corpus=value;
            else {size_t end=0;auto n=std::stoul(value,&end);require(end==value.size()&&n<=16,"numeric option bound");if(arg=="--device")selected=n;else throw std::runtime_error("unknown option "+arg);}
        }
    }
    extension_gate_test();
    auto cases=generated(full,maximum);rowblocks_gpu::append_corpus(cases,argv[2],"golden");size_t corpus_cases=cases.size();if(!corpus.empty())rowblocks_gpu::append_corpus(cases,corpus,"corpus");corpus_cases=cases.size()-corpus_cases;
    std::array<unsigned,3> counts{};
    for(auto& fixture:cases)for(unsigned eye=0;eye<2;++eye) {
        auto index=rowblocks::inspect(fixture.wire[eye]);for(const auto& block:index.blocks)++counts[block.mode];
        std::vector<uint32_t> decoded(fixture.expected[eye].size());rowblocks::Workspace workspace;
        rowblocks::decode_into(fixture.wire[eye],decoded,workspace);require(decoded==fixture.expected[eye],"CPU fixture mismatch");
        auto bad=fixture.wire[eye];bad.back()^=1;bool rejected=false;
        try{rowblocks::unpack_rgb(bad,workspace);}catch(const std::exception&){rejected=true;}
        require(rejected&&workspace.rgb.empty(),"failed unpack retained output");
    }
    for(auto count:counts)require(count>0,"all RRB block modes required");
    if(cpu_only){std::cout<<"{\"cpu_fixture_pass\":true,\"fixture_count\":"<<cases.size()<<",\"rrb_block_modes\":3,\"vulkan_initialized\":false,\"headset_used\":false}\n";return 0;}
    auto shader_path=std::filesystem::path(argv[1])/"shaders";
    std::unique_ptr<Session> session;
    std::cout<<"{\"schema\":1,\"experiment\":\"rowblocks-gpu\",\"headset_used\":false,\"format_list\":"<<(format_list?"true":"false")<<",\"full_fixture_matrix\":"<<(full?"true":"false")<<",\"maximum_fixtures\":"<<(maximum?"true":"false")<<",\"corpus_cases\":"<<corpus_cases<<",\"cpu_prefix_restore_in_path\":false,\"shared_prefix_barriers\":false,\"workgroup_columns\":128,\"cpu_grammar_crc_lz4_in_path\":true,\"rrb_block_modes\":3,\"rrb_block_mode_counts\":["<<counts[0]<<","<<counts[1]<<","<<counts[2]<<"],\"warmups\":1,\"repetitions\":1,\"cases\":["<<std::flush;
    unsigned index=0;
    for(const auto& fixture:cases) {
        if(!session||session->width!=fixture.width||session->height!=fixture.height)
            session=std::make_unique<Session>(fixture.width,fixture.height,shader_path,selected,false,format_list,8);
        auto& s=*session;require(s.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_INTEGRATED_GPU||s.properties.deviceType==VK_PHYSICAL_DEVICE_TYPE_DISCRETE_GPU,"hardware GPU required");
        std::array<std::vector<float>,2> baseline;
        auto left=rowblocks::inspect(fixture.wire[0]),right=rowblocks::inspect(fixture.wire[1]);
        auto block_modes=[](const rowblocks::Index& index){std::string out="[";for(const auto& block:index.blocks){if(out.size()>1)out+=",";out+=std::to_string(block.mode);}return out+"]";};
        unsigned groups=((fixture.width+127)/128)*std::max(left.blocks.size(),right.blocks.size());
        auto bad=fixture;bad.wire[1].back()^=1;unsigned before=s.submissions;bool rejected=false;
        std::memset(s.o.inputs[0][0].mapped,0xcd,64);
        try{run(s,bad,0);}catch(const std::exception&){rejected=true;}
        auto* sentinel=static_cast<uint8_t*>(s.o.inputs[0][0].mapped);
        require(rejected&&s.submissions==before&&std::all_of(sentinel,sentinel+64,[](auto b){return b==0xcd;}),"malformed right eye reached writes/submission");
        Prepared admission;unsigned lifecycle_rejections=0;
        try{admission.upload(s);}catch(const std::exception&){++lifecycle_rejections;}
        try{admission.admit(s,bad);}catch(const std::exception&){++lifecycle_rejections;}
        try{admission.upload(s);}catch(const std::exception&){++lifecycle_rejections;}
        auto mismatch=fixture;rowblocks::Workspace encode_work;unsigned other_width=fixture.width==1?2:1;
        std::vector<uint32_t> other(other_width,0xff010203);auto other_wire=rowblocks::encode(other,other_width,1,16,rowfilter::Policy::up,encode_work);
        mismatch.wire[1].assign(other_wire.begin(),other_wire.end());bool shape_rejected=false;
        try{run(s,mismatch,0);}catch(const std::exception&){shape_rejected=true;}
        require(lifecycle_rejections==3&&shape_rejected&&s.submissions==before&&std::all_of(sentinel,sentinel+64,[](auto b){return b==0xcd;}),"RRB admission lifetime or stereo shape gate");
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(fixture.name)<<",\"device\":"<<quote(s.properties.deviceName)<<",\"device_type\":"<<s.properties.deviceType<<",\"driver\":"<<s.properties.driverVersion<<",\"input_memory_flags\":"<<s.o.inputs[0][0].flags<<",\"width\":"<<fixture.width<<",\"height\":"<<fixture.height<<",\"dispatch_groups\":"<<groups<<",\"eye_block_rows\":["<<left.block_rows<<","<<right.block_rows<<"],\"eye_block_counts\":["<<left.blocks.size()<<","<<right.blocks.size()<<"],\"eye_block_modes\":["<<block_modes(left)<<","<<block_modes(right)<<"],\"stereo_raw_rgb_bytes\":"<<uint64_t(fixture.width)*fixture.height*6<<",\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<2;++rep)for(unsigned order=0;order<modes;++order) {
            unsigned mode=rep?(index+order)%modes:order;auto m=run(s,fixture,mode);
            for(unsigned eye=0;eye<2;++eye){auto* samples=static_cast<float*>(s.o.linear[eye].mapped);
                if(!rep&&!mode)baseline[eye].assign(samples,samples+size_t(fixture.width)*fixture.height*4);
                else require(!std::memcmp(samples,baseline[eye].data(),s.o.linear[eye].bytes),"RRB sampled values differ from same-device baseline");}
            if(!rep)continue;
            std::cout<<(first?"":",")<<"{\"mode\":"<<quote(names[mode])<<",\"validation_lz4_ms\":"<<m.validation_ms<<",\"input_copy_ms\":"<<m.input_copy_ms<<",\"stereo_gpu_ms\":"<<m.gpu_ms<<",\"pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"oracle_ms\":"<<m.oracle_ms<<",\"max_linear_error\":"<<m.max_linear_error<<",\"max_srgb_code_error\":"<<m.max_srgb_code_error<<",\"bytes_exact\":true,\"one_eotf\":true,\"sampled_baseline_exact\":true,\"malformed_pair_rejected_before_write_or_submit\":true,\"stereo_shape_rejected_before_write_or_submit\":true,\"upload_after_failed_admission_rejected\":true}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"promotion_blocked\":true,\"scope\":\"Complete CPU CRC/grammar/LZ4 admission plus GPU independent-block serial-column expansion; host correctness only, no XR or performance promotion\"}\n";return 0;
}catch(const std::exception& e){std::cerr<<"rowblocks-gpu: "<<e.what()<<'\n';return 1;}}
