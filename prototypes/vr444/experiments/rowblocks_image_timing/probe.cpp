// SPDX-License-Identifier: MIT
#include "fixtures.hpp"
using namespace rowblocks_image;
int main(int argc,char** argv){try{
    require(argc>=4,"usage: rowblocks-image SHADERS_ROOT RRF_CORPUS RRB_CORPUS [--reps N] [--device N] [--qualify RRF_GOLDENS]");
    unsigned reps=31,selected=0;std::filesystem::path goldens;
    for(int i=4;i<argc;++i){std::string option=argv[i];require(i+1<argc,"missing option");std::string value=argv[++i];
        if(option=="--qualify")goldens=value;
        else{size_t end=0;auto n=std::stoul(value,&end);require(end==value.size()&&n<=63,"numeric option bound");if(option=="--reps")reps=n;else if(option=="--device")selected=n;else throw std::runtime_error("unknown option");}}
    require(reps>=1,"repetitions");bool qualify=!goldens.empty();if(qualify)reps=1;unsigned warmups=qualify?1:3;
    auto cases=qualify?qualification(goldens,argv[2]):paired(argv[2],argv[3]);
    std::cout<<"{\"schema\":1,\"experiment\":\"rowblocks-image-timing\",\"qualification_only\":"<<(qualify?"true":"false")<<",\"headset_used\":false,\"promotion_blocked\":true,\"warmups\":"<<warmups<<",\"repetitions\":"<<reps<<",\"cpu_backend\":"<<quote(rowfilter::detail::backend())<<",\"checked_crc_grammar_lz4_in_every_arm\":true,\"poison_in_total_time\":true,\"oracle_outside_total_time\":true,\"reused_workspaces\":true,\"cases\":["<<std::flush;
    std::vector<std::unique_ptr<Engine>> engines;unsigned width=0,height=0,index=0;
    for(const auto& profile:cases){const auto& base=profile.variants[0];
        if(width!=base.width||height!=base.height){engines.clear();width=base.width;height=base.height;
            for(unsigned v=0;v<5;++v)for(bool cpu:{true,false})engines.push_back(std::make_unique<Engine>(width,height,argv[1],v,cpu,selected));}
        const auto& properties=engines[0]->session().properties;std::array<std::vector<float>,2> baseline;
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
        }
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(base.name)<<",\"synthetic_duplicated_eyes\":"<<(base.name.starts_with("corpus/synthetic/")?"true":"false")<<",\"device\":"<<quote(properties.deviceName)<<",\"device_type\":"<<properties.deviceType<<",\"driver\":"<<properties.driverVersion<<",\"width\":"<<width<<",\"height\":"<<height<<",\"variants\":[";
        for(unsigned v=0;v<5;++v){const auto& f=profile.variants[v];std::array<unsigned,2> rows{},counts{1,1};
            if(v)for(unsigned eye=0;eye<2;++eye){auto ix=rowblocks::inspect(f.wire[eye]);rows[eye]=ix.block_rows;counts[eye]=ix.blocks.size();}
            std::cout<<(v?",":"")<<"{\"variant\":"<<v<<",\"eye_wire_bytes\":["<<f.wire[0].size()<<","<<f.wire[1].size()<<"],\"eye_block_rows\":["<<rows[0]<<","<<rows[1]<<"],\"eye_block_counts\":["<<counts[0]<<","<<counts[1]<<"]}";
        }
        std::cout<<"],\"iterations\":[";bool first=true;
        for(unsigned rep=0;rep<reps+warmups;++rep)for(unsigned order=0;order<engines.size();++order){unsigned arm=rep?((index+rep+order)%engines.size()):order;auto& engine=*engines[arm];auto& s=engine.session();const auto& fixture=profile.variants[engine.variant];
            auto r=engine.run(fixture);auto oracle=s.verify(engine.mode(),fixture.expected);
            for(unsigned eye=0;eye<2;++eye){auto* values=static_cast<float*>(s.o.linear[eye].mapped);
                if(!rep&&!arm)baseline[eye].assign(values,values+size_t(width)*height*4);
                else require(!std::memcmp(values,baseline[eye].data(),s.o.linear[eye].bytes),"same-device RRF CPU baseline mismatch");}
            if(rep<warmups)continue;const auto& m=r.stages;
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-warmups<<",\"order\":"<<order<<",\"mode\":"<<quote(engine.name)<<",\"variant\":"<<engine.variant<<",\"stereo_total_ms\":"<<r.total_ms<<",\"stereo_poison_ms\":"<<r.poison_ms<<",\"stereo_preflight_decode_ms\":"<<m.validation_ms<<",\"stereo_upload_ms\":"<<m.input_copy_ms<<",\"stereo_pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"stereo_device_span_ms\":"<<m.gpu_ms<<",\"stereo_uploaded_pixel_bytes\":"<<r.uploaded_bytes<<",\"timed_queue_submissions\":"<<r.submissions<<",\"max_srgb_code_error\":"<<oracle.max_srgb_code_error<<",\"image_bytes_exact\":true,\"sampled_cpu_copy_baseline_exact\":true,\"malformed_pair_rejected_before_write_or_submit\":true,\"wrong_shape_rejected_before_write_or_submit\":true}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Complete checked RRF/RRB packets to observed stereo shader-readable images; diagnostic poison included, no server/network/XR/headset promotion\"}\n";return 0;
}catch(const std::exception& e){std::cerr<<"rowblocks-image: "<<e.what()<<'\n';return 1;}}
