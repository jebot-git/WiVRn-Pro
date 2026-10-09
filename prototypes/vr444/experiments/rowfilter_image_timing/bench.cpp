// SPDX-License-Identifier: MIT
#include "pipeline.hpp"
#include <optional>
using namespace rowfilter_image;
int main(int argc,char** argv) {try {
    require(argc>=3,"usage: rowfilter-image SHADERS_ROOT CORPUS_OR_GOLDENS [--reps N] [--device N] [--qualify] [--corpus DIR]");
    bool qualification=false;unsigned reps=31,selected=0;std::filesystem::path extra;
    for(int i=3;i<argc;++i) {
        std::string arg=argv[i];if(arg=="--qualify")qualification=true;
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
    std::cout<<"{\"schema\":1,\"experiment\":\"rowfilter-image-timing\",\"qualification_only\":"<<(qualification?"true":"false")<<",\"headset_used\":false,\"promotion_blocked\":true,\"warmups\":"<<warmups<<",\"repetitions\":"<<reps<<",\"source_corpus_cases\":"<<source_count<<",\"real_capture_pairs\":"<<real_pairs<<",\"synthetic_duplicated_eye_cases\":"<<synthetic_cases<<",\"cpu_backend\":"<<quote(rowfilter::detail::backend())<<",\"checked_crc_grammar_lz4_in_every_arm\":true,\"poison_in_total_time\":true,\"oracle_outside_total_time\":true,\"reused_workspaces\":true,\"cases\":["<<std::flush;
    std::vector<std::unique_ptr<ImagePath>> engines;unsigned width=0,height=0,index=0;
    for(const auto& fixture:cases) {
        if(width!=fixture.width||height!=fixture.height) {
            engines.clear();width=fixture.width;height=fixture.height;
            engines.push_back(std::make_unique<ImagePath>(width,height,argv[1],4,0,true,selected));
            for(unsigned columns:{1u,4u,16u})for(unsigned mode:{1u,2u})engines.push_back(std::make_unique<ImagePath>(width,height,argv[1],columns,mode,false,selected));
        }
        const auto& properties=engines[0]->session.properties;std::array<std::vector<float>,2> baseline;
        for(auto& engine:engines) {
            require(std::string(engine->session.properties.deviceName)==properties.deviceName&&engine->session.properties.driverVersion==properties.driverVersion,"same physical device/driver");
            auto bad=fixture;bad.wire[1].back()^=1;auto before=engine->session.submissions;bool rejected=false;
            auto* sentinel=static_cast<uint8_t*>(engine->session.o.inputs[0][0].mapped);std::memset(sentinel,0xcd,64);
            try{engine->run(bad);}catch(const std::exception&){rejected=true;}
            require(rejected&&engine->session.submissions==before&&std::all_of(sentinel,sentinel+64,[](auto b){return b==0xcd;}),"malformed right eye reached image pipeline");
        }
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(fixture.name)<<",\"synthetic_duplicated_eyes\":"<<(fixture.name.starts_with("corpus/synthetic/")?"true":"false")<<",\"device\":"<<quote(properties.deviceName)<<",\"device_type\":"<<properties.deviceType<<",\"driver\":"<<properties.driverVersion<<",\"width\":"<<width<<",\"height\":"<<height<<",\"eye_wire_bytes\":["<<fixture.wire[0].size()<<","<<fixture.wire[1].size()<<"],\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<reps+warmups;++rep)for(unsigned order=0;order<engines.size();++order) {
            unsigned arm=rep==0?order:(index+rep+order)%engines.size();auto& engine=*engines[arm];auto result=engine.run(fixture);
            // Readback, independent exact RGBA/color oracle and same-physical-
            // device sample comparison are outside the whole readiness clock.
            auto oracle=engine.verify(fixture);
            for(unsigned eye=0;eye<2;++eye) {
                auto* values=static_cast<float*>(engine.session.o.linear[eye].mapped);
                if(!rep&&!arm)baseline[eye].assign(values,values+size_t(width)*height*4);
                else require(!std::memcmp(values,baseline[eye].data(),engine.session.o.linear[eye].bytes),"CPU-copy/GPU sampled image mismatch");
            }
            if(rep<warmups)continue;
            auto& m=result.stages;
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-warmups<<",\"order\":"<<order<<",\"mode\":"<<quote(engine.name())<<",\"stereo_total_ms\":"<<result.total_ms<<",\"stereo_poison_ms\":"<<result.poison_ms<<",\"stereo_preflight_decode_ms\":"<<m.validation_ms<<",\"stereo_upload_ms\":"<<m.input_copy_ms<<",\"stereo_pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"stereo_device_span_ms\":"<<m.gpu_ms<<",\"stereo_uploaded_pixel_bytes\":"<<result.uploaded_bytes<<",\"timed_queue_submissions\":"<<result.submissions<<",\"max_srgb_code_error\":"<<oracle.max_srgb_code_error<<",\"image_bytes_exact\":true,\"sampled_cpu_copy_baseline_exact\":true,\"malformed_pair_rejected_before_write_or_submit\":true}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Checked RRF1 packet to observed shader-readable stereo image; poison included and separately measured; private host GPU, no network/XR/headset/sub15 promotion\"}\n";return 0;
}catch(const std::exception& e){std::cerr<<"rowfilter-image: "<<e.what()<<'\n';return 1;}}
