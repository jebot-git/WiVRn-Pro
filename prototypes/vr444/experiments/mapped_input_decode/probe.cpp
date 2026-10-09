// SPDX-License-Identifier: MIT
#include "engine.hpp"
using namespace mapped_input;
using rowblocks_image::qualification;
using rowblocks_image::paired;
unsigned memory_gate_tests(){
    unsigned rejected=0;VkPhysicalDeviceMemoryProperties memory{};
    auto fail=[&](uint32_t bits,unsigned flags){bool bad=false;try{memory_index(memory,bits,flags);}catch(const std::exception&){bad=true;}require(bad,"memory gate should reject");++rejected;};
    fail(UINT32_MAX,14);memory.memoryTypeCount=2;memory.memoryTypes[0].propertyFlags=6;memory.memoryTypes[1].propertyFlags=14;
    require(memory_index(memory,3,6)==0&&memory_index(memory,3,14)==1,"default/cached type choice");
    fail(1,14);fail(0,6);memory.memoryTypes[1].propertyFlags=12;fail(2,14);memory.memoryTypeCount=VK_MAX_MEMORY_TYPES+1;fail(3,6);return rejected;
}
int fail_decoder(const char*,char* out,int,int n){if(n)out[0]=char(0xee);return -1;}
bool compressed(const Fixture& f,bool blocks){
    for(const auto& wire:f.wire){if(blocks){for(const auto& b:rowblocks::inspect(wire).blocks)if(b.mode)return true;}
        else if(rowfilter::inspect(wire).mode)return true;}
    return false;
}
int main(int argc,char** argv){try{
    require(argc>=4,"usage: mapped-input SHADERS_ROOT RRF_CORPUS RRB_CORPUS [--reps N] [--device N] [--qualify RRF_GOLDENS]");
    unsigned reps=31,selected=0;std::filesystem::path goldens;
    for(int i=4;i<argc;++i){std::string option=argv[i];require(i+1<argc,"missing option");std::string value=argv[++i];
        if(option=="--qualify")goldens=value;
        else{size_t end=0;auto n=std::stoul(value,&end);require(end==value.size()&&n<=63,"numeric option bound");if(option=="--reps")reps=n;else if(option=="--device")selected=n;else throw std::runtime_error("unknown option");}}
    require(memory_gate_tests()==5,"memory negative gates");require(reps>=1,"repetitions");bool qualify=!goldens.empty();if(qualify)reps=1;unsigned warmups=qualify?1:3;
    auto cases=qualify?qualification(goldens,argv[2]):paired(argv[2],argv[3]);
    std::cout<<"{\"schema\":1,\"experiment\":\"mapped-input-decode\",\"qualification_only\":"<<(qualify?"true":"false")<<",\"headset_used\":false,\"promotion_blocked\":true,\"warmups\":"<<warmups<<",\"repetitions\":"<<reps<<",\"cpu_backend\":"<<quote(rowfilter::detail::backend())<<",\"checked_crc_grammar_lz4_in_every_arm\":true,\"poison_in_total_time\":true,\"oracle_outside_total_time\":true,\"reused_workspaces\":true,\"memory_gate_rejections\":5,\"cases\":["<<std::flush;
    std::vector<std::unique_ptr<Engine>> engines;unsigned width=0,height=0,index=0;
    for(const auto& profile:cases){const auto& base=profile.variants[0];
        if(width!=base.width||height!=base.height){engines.clear();width=base.width;height=base.height;
            for(unsigned arm=0;arm<9;++arm)engines.push_back(std::make_unique<Engine>(width,height,argv[1],arm,selected));}
        const auto& properties=engines[0]->session().properties;std::array<std::vector<float>,2> baseline;std::array<bool,9> fault_tested{};
        for(auto& engine:engines){auto& s=engine->session();require(std::string(s.properties.deviceName)==properties.deviceName&&s.properties.driverVersion==properties.driverVersion,"same physical device");
            const auto& fixture=profile.variants[engine->variant];auto bad=fixture;bad.wire[1].back()^=1;unsigned before=s.submissions;bool rejected=false;
            auto* sentinel=static_cast<uint8_t*>(s.o.inputs[0][0].mapped);std::memset(sentinel,0xcd,64);
            try{engine->run(bad);}catch(const std::exception&){rejected=true;}
            require(rejected&&s.submissions==before&&std::all_of(sentinel,sentinel+64,[](auto b){return b==0xcd;}),"malformed pair reached mapped/image work");
            auto mismatch=fixture;std::vector<uint32_t> other(width==1?2:1,0xff030201);
            if(engine->variant){rowblocks::Workspace w;auto wire=rowblocks::encode(other,other.size(),1,16,rowfilter::Policy::up,w);mismatch.wire[1].assign(wire.begin(),wire.end());}
            else{rowfilter::Workspace w;auto wire=rowfilter::encode(other,other.size(),1,rowfilter::Policy::up,w);mismatch.wire[1].assign(wire.begin(),wire.end());}
            rejected=false;try{engine->run(mismatch);}catch(const std::exception&){rejected=true;}
            require(rejected&&s.submissions==before&&std::all_of(sentinel,sentinel+64,[](auto b){return b==0xcd;}),"wrong shape reached mapped/image work");
            if(engine->direct&&compressed(fixture,engine->variant!=0)){
                fault_tested[engine->arm]=true;before=s.submissions;rejected=false;try{engine->mapped->run(fixture,fail_decoder);}catch(const std::exception&){rejected=true;}
                require(rejected&&s.submissions==before+1,"partial mapped decode reached readiness submission");
                for(const auto& image:s.o.images[1])require(image.layout==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,"failed decode image became ready");
            }
        }
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(base.name)<<",\"synthetic_duplicated_eyes\":"<<(base.name.starts_with("corpus/synthetic/")?"true":"false")<<",\"device\":"<<quote(properties.deviceName)<<",\"device_type\":"<<properties.deviceType<<",\"driver\":"<<properties.driverVersion<<",\"width\":"<<width<<",\"height\":"<<height<<",\"variants\":[";
        for(unsigned v:{0u,2u}){const auto& f=profile.variants[v];std::array<unsigned,2> rows{},counts{1,1};
            if(v)for(unsigned eye=0;eye<2;++eye){auto ix=rowblocks::inspect(f.wire[eye]);rows[eye]=ix.block_rows;counts[eye]=ix.blocks.size();}
            std::cout<<(v?",":"")<<"{\"variant\":"<<v<<",\"eye_wire_bytes\":["<<f.wire[0].size()<<","<<f.wire[1].size()<<"],\"eye_block_rows\":["<<rows[0]<<","<<rows[1]<<"],\"eye_block_counts\":["<<counts[0]<<","<<counts[1]<<"],\"compressed_present\":"<<(compressed(f,v!=0)?"true":"false")<<"}";
        }
        std::cout<<"],\"iterations\":[";bool first=true;
        for(unsigned rep=0;rep<reps+warmups;++rep)for(unsigned order=0;order<engines.size();++order){unsigned arm=rep?((index+rep+order)%engines.size()):order;auto& engine=*engines[arm];auto& s=engine.session();const auto& fixture=profile.variants[engine.variant];
            auto result=engine.run(fixture);auto& r=result.readiness;auto oracle=s.verify(engine.mode(),fixture.expected);
            for(unsigned eye=0;eye<2;++eye){auto* values=static_cast<float*>(s.o.linear[eye].mapped);
                if(!rep&&!arm)baseline[eye].assign(values,values+size_t(width)*height*4);
                else require(!std::memcmp(values,baseline[eye].data(),s.o.linear[eye].bytes),"same-device RRF CPU baseline mismatch");}
            if(rep<warmups)continue;const auto& m=r.stages;
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-warmups<<",\"order\":"<<order<<",\"mode\":"<<quote(mapped_input::names[engine.arm])<<",\"variant\":"<<engine.variant<<",\"stereo_total_ms\":"<<r.total_ms<<",\"stereo_poison_ms\":"<<r.poison_ms<<",\"stereo_preflight_decode_ms\":"<<m.validation_ms<<",\"stereo_upload_ms\":"<<m.input_copy_ms<<",\"stereo_pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"stereo_device_span_ms\":"<<m.gpu_ms<<",\"stereo_uploaded_pixel_bytes\":"<<r.uploaded_bytes<<",\"timed_queue_submissions\":"<<r.submissions<<",\"intermediate_pixel_copy_bytes\":"<<result.intermediate_pixel_copy_bytes<<",\"mapped_output_bytes\":"<<result.mapped_output_bytes<<",\"metadata_bytes\":"<<result.metadata_bytes<<",\"split_decode_measured\":"<<(result.split_decode?"true":"false")<<",\"direct_preflight_ms\":"<<result.preflight_ms<<",\"direct_decode_ms\":"<<result.decode_ms<<",\"input_memory_type\":["<<s.o.inputs[0][0].type<<","<<s.o.inputs[1][0].type<<"],\"input_memory_flags\":["<<s.o.inputs[0][0].flags<<","<<s.o.inputs[1][0].flags<<"],\"cached_requested\":"<<(engine.cached?"true":"false")<<",\"max_srgb_code_error\":"<<oracle.max_srgb_code_error<<",\"image_bytes_exact\":true,\"sampled_cpu_copy_baseline_exact\":true,\"malformed_pair_rejected_before_write_or_submit\":true,\"wrong_shape_rejected_before_write_or_submit\":true,\"late_decoder_gate_exercised\":"<<(fault_tested[engine.arm]?"true":"false")<<"}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Checked staged/direct mapped RRF/RRB32 inputs to observed stereo image readiness; poison included; no server/network/XR/headset promotion\"}\n";return 0;
}catch(const std::exception& e){std::cerr<<"mapped-input: "<<e.what()<<'\n';return 1;}}
