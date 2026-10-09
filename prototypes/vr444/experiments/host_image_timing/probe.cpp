// SPDX-License-Identifier: MIT
#include "consumer.hpp"
#include <optional>
using namespace host_image_timing;
int main(int argc,char** argv) {try {
    require(argc>=3,"usage: host-image-copy SHADERS_ROOT CORPUS_OR_GOLDENS [--reps N] [--device N] [--qualify] [--corpus DIR] [--fault-consumer]");
    bool qualification=false,fault_consumer=false;unsigned reps=31,selected=0;std::filesystem::path extra;
    for(int i=3;i<argc;++i) {
        std::string arg=argv[i];if(arg=="--qualify")qualification=true;else if(arg=="--fault-consumer")fault_consumer=true;
        else {require(i+1<argc,"missing option");std::string value=argv[++i];
            if(arg=="--corpus")extra=value;
            else {size_t end=0;auto n=std::stoul(value,&end);require(end==value.size()&&n<=63,"numeric option bound");
                if(arg=="--reps")reps=n;else if(arg=="--device")selected=n;else throw std::runtime_error("unknown option "+arg);}
        }
    }
    require(reps>=1,"repetitions");require(qualification||extra.empty(),"extra corpus belongs to qualification");
    unsigned source_count=0,real_pairs=0,synthetic_cases=0;std::vector<Fixture> cases;
    if(qualification) {
        reps=1;cases=generated(true,true);append_corpus(cases,argv[2],"golden");if(!extra.empty())append_corpus(cases,extra,"corpus");
    } else {
        std::vector<Fixture> singles;append_corpus(singles,argv[2],"corpus");source_count=singles.size();
        std::map<std::string,std::array<std::optional<Fixture>,2>> pairs;
        for(auto& f:singles) {
            if(f.name.starts_with("corpus/synthetic/")){cases.push_back(std::move(f));++synthetic_cases;continue;}
            require(f.name.ends_with("-0.rrf")||f.name.ends_with("-1.rrf"),"captured eye suffix");
            auto name=f.name.substr(0,f.name.size()-6);unsigned eye=f.name.ends_with("-1.rrf");
            auto& slot=pairs[name][eye];require(!slot,"duplicate captured eye");slot=std::move(f);
        }
        real_pairs=pairs.size();require(real_pairs>0,"real captured pairs required");
        for(auto& [name,pair]:pairs) {
            require(pair[0]&&pair[1],"incomplete captured pair");auto& left=*pair[0];auto& right=*pair[1];require(left.width==right.width&&left.height==right.height,"captured pair shape");
            cases.push_back({name,left.width,left.height,{std::move(left.wire[0]),std::move(right.wire[0])},{std::move(left.expected[0]),std::move(right.expected[0])}});
        }
    }
    const unsigned warmups=qualification?1:3;
    std::cout<<"{\"schema\":1,\"experiment\":\"host-image-consumer-timing\",\"qualification_only\":"<<(qualification?"true":"false")<<",\"headset_used\":false,\"promotion_blocked\":true,\"warmups\":"<<warmups<<",\"repetitions\":"<<reps<<",\"source_corpus_cases\":"<<source_count<<",\"real_capture_pairs\":"<<real_pairs<<",\"synthetic_duplicated_eye_cases\":"<<synthetic_cases<<",\"cpu_backend\":"<<quote(rowfilter::detail::backend())<<",\"checked_crc_grammar_lz4_in_every_arm\":true,\"poison_in_total_time\":true,\"oracle_outside_total_time\":true,\"reused_workspaces\":true,\"cases\":["<<std::flush;
    std::vector<std::unique_ptr<ImagePath>> engines;unsigned width=0,height=0,index=0;
    for(const auto& fixture:cases) {
        if(width!=fixture.width||height!=fixture.height) {
            engines.clear();width=fixture.width;height=fixture.height;
            engines.push_back(std::make_unique<ImagePath>(width,height,argv[1],4,0,true,selected));
            engines.push_back(std::make_unique<ImagePath>(width,height,argv[1],4,0,true,selected,true));
        }
        const auto& properties=engines[0]->session.properties;std::array<std::vector<float>,2> baseline;
        const auto& capability=engines[1]->session.host.evidence;
        auto image_memory=[](const ImagePath& path){std::string out="[";for(const auto& image:path.session.o.images[0]){if(out.size()>1)out+=",";out+="{\"type\":"+std::to_string(image.memory_type)+",\"flags\":"+std::to_string(image.memory_flags)+",\"type_bits\":"+std::to_string(image.memory_type_bits)+",\"allocation_bytes\":"+std::to_string(image.allocation_bytes)+"}";}return out+"]";};
        auto layouts=[](const std::vector<VkImageLayout>& list){std::string out="[";for(auto layout:list){if(out.size()>1)out+=",";out+=std::to_string(layout);}return out+"]";};
        // A host upload before completed poison or with a malformed right shape
        // must be rejected without a host transition or submission.
        auto& hs=engines[1]->session;bool early=false;try{hs.host_upload(fixture.expected);}catch(const std::exception&){early=true;}
        require(early,"host copy admitted without availability proof");
        hs.poison(0);auto invalid_shape=fixture.expected;invalid_shape[1].pop_back();auto before_shape=hs.submissions;bool shape_rejected=false;
        try{hs.host_upload(invalid_shape);}catch(const std::exception&){shape_rejected=true;}
        require(shape_rejected&&hs.submissions==before_shape&&hs.host_image_available&&hs.o.images[0][0].layout==VK_IMAGE_LAYOUT_TRANSFER_DST_OPTIMAL,"host shape failure changed image state");
        for(auto& engine:engines) {
            require(std::string(engine->session.properties.deviceName)==properties.deviceName&&engine->session.properties.driverVersion==properties.driverVersion,"same physical device/driver");
            auto bad=fixture;bad.wire[1].back()^=1;auto before=engine->session.submissions;bool rejected=false;
            auto* sentinel=static_cast<uint8_t*>(engine->session.o.inputs[0][0].mapped);std::memset(sentinel,0xcd,64);
            try{engine->run(bad);}catch(const std::exception&){rejected=true;}
            require(rejected&&engine->session.submissions==before&&std::all_of(sentinel,sentinel+64,[](auto b){return b==0xcd;}),"malformed right eye reached image pipeline");
        }
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(fixture.name)<<",\"synthetic_duplicated_eyes\":"<<(fixture.name.starts_with("corpus/synthetic/")?"true":"false")<<",\"device\":"<<quote(properties.deviceName)<<",\"device_type\":"<<properties.deviceType<<",\"driver\":"<<properties.driverVersion<<",\"width\":"<<width<<",\"height\":"<<height<<",\"eye_wire_bytes\":["<<fixture.wire[0].size()<<","<<fixture.wire[1].size()<<"],\"host_copy_capability\":{\"queried\":true,\"dependencies_enabled\":true,\"feature_enabled\":true,\"format_supported\":true,\"combined_usage_supported\":true,\"usage\":"<<capability.usage<<",\"source_layouts\":"<<layouts(capability.sources)<<",\"destination_layouts\":"<<layouts(capability.destinations)<<",\"optimal_device_access\":"<<(capability.optimal_device_access?"true":"false")<<",\"identical_memory_layout\":"<<(capability.identical_memory_layout?"true":"false")<<",\"identical_memory_type_requirements\":"<<(capability.identical_memory_type_requirements?"true":"false")<<"},\"staging_image_memory\":"<<image_memory(*engines[0])<<",\"host_image_memory\":"<<image_memory(*engines[1])<<",\"host_availability_and_shape_rejections\":true,\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<reps+warmups;++rep)for(unsigned order=0;order<engines.size();++order) {
            unsigned arm=rep==0?order:(index+rep+order)%engines.size();auto& engine=*engines[arm];auto joint_start=Clock::now();auto result=engine.run(fixture);
            auto consumer=sample(engine,fault_consumer);double joint_ms=elapsed(joint_start);
            std::array<std::vector<float>,2> first_consumer;
            for(unsigned eye=0;eye<2;++eye){auto* values=static_cast<float*>(engine.session.o.linear[eye].mapped);first_consumer[eye].assign(values,values+size_t(width)*height*4);}
            // Readback, independent exact RGBA/color oracle and same-physical-
            // device sample comparison are outside the whole readiness clock.
            auto oracle=engine.verify(fixture);
            for(unsigned eye=0;eye<2;++eye) {
                auto* values=static_cast<float*>(engine.session.o.linear[eye].mapped);
                if(!rep&&!arm)baseline[eye].assign(values,values+size_t(width)*height*4);
                else require(!std::memcmp(values,baseline[eye].data(),engine.session.o.linear[eye].bytes),"CPU-copy/host-copy sampled image mismatch");
                require(!std::memcmp(first_consumer[eye].data(),baseline[eye].data(),engine.session.o.linear[eye].bytes),"first consumer differs from verified baseline");
            }
            if(rep<warmups)continue;
            auto& m=result.stages;
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-warmups<<",\"order\":"<<order<<",\"mode\":"<<quote(engine.name())<<",\"stereo_total_ms\":"<<result.total_ms<<",\"stereo_poison_ms\":"<<result.poison_ms<<",\"stereo_preflight_decode_ms\":"<<m.validation_ms<<",\"stereo_upload_ms\":"<<m.input_copy_ms<<",\"stereo_pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"stereo_device_span_ms\":"<<m.gpu_ms<<",\"stereo_uploaded_pixel_bytes\":"<<result.uploaded_bytes<<",\"timed_queue_submissions\":"<<result.submissions<<",\"stereo_ready_and_sampled_ms\":"<<joint_ms<<",\"stereo_consumer_wall_ms\":"<<consumer.wall_ms<<",\"stereo_consumer_device_ms\":"<<consumer.gpu_ms<<",\"consumer_queue_submissions\":"<<consumer.submissions<<",\"consumer_sampled_baseline_exact\":true,\"max_srgb_code_error\":"<<oracle.max_srgb_code_error<<",\"image_bytes_exact\":true,\"sampled_cpu_copy_baseline_exact\":true,\"malformed_pair_rejected_before_write_or_submit\":true}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Checked RRF1 packet to observed shader-readable stereo image; poison included and separately measured; private host GPU, no network/XR/headset/sub15 promotion\"}\n";return 0;
}catch(const host_copy::Unsupported& e){std::cerr<<"unsupported: "<<e.what()<<'\n';return 2;}catch(const std::exception& e){std::cerr<<"host-image-copy: "<<e.what()<<'\n';return 1;}}
