// SPDX-License-Identifier: MIT
#include "../palette_gpu_encode/fixtures.hpp"
#include <optional>
using namespace palette_encode;
using namespace palette_fixtures;
int main(int argc,char** argv) { try {
    require(argc==3||argc==4,"usage: palette-bench SHADERS EXPORTED_CAPTURE_DIR [REPS]");
    unsigned reps=31;if(argc==4) { size_t end=0;auto n=std::stoul(argv[3],&end);require(end==std::strlen(argv[3])&&n>=1&&n<=63,"repetitions bound");reps=n; }
    std::vector<Fixture> singles;corpus(singles,argv[2],"corpus");require(!singles.empty(),"corpus inputs required");
    const auto source_cases=singles.size();std::vector<Fixture> cases;unsigned synthetic_cases=0;
    std::map<std::string,std::array<std::optional<Fixture>,2>> pairs;
    for(auto& f:singles) {
        if(f.name.starts_with("corpus/synthetic/")) { cases.push_back(std::move(f));++synthetic_cases;continue; }
        require(f.name.ends_with("-0.rpt")||f.name.ends_with("-1.rpt"),"unrecognized captured eye suffix");
        unsigned eye=f.name.ends_with("-1.rpt");auto name=f.name.substr(0,f.name.size()-6);auto& slot=pairs[name][eye];require(!slot,"duplicate captured eye");slot=std::move(f);
    }
    const auto real_pairs=pairs.size();require(real_pairs>0,"real captured pairs required");
    for(auto& [name,pair]:pairs) {
        require(pair[0]&&pair[1],"incomplete captured stereo pair");auto& l=*pair[0];auto& r=*pair[1];require(l.width==r.width&&l.height==r.height,"captured stereo shape mismatch");
        cases.push_back({name,l.width,l.height,{std::move(l.pixels[0]),std::move(r.pixels[0])},{std::move(l.reference[0]),std::move(r.reference[0])}});
    }
    singles.clear();pairs.clear();require(cases.size()<=64,"paired corpus bound");
    std::map<std::pair<unsigned,unsigned>,std::array<std::unique_ptr<Encoder>,3>> sessions;
    constexpr std::array<OutputMode,3> modes{OutputMode::full_copy,OutputMode::mapped,OutputMode::compact_copy};
    std::cout<<"{\"schema\":1,\"experiment\":\"palette-host-timing\",\"headset_used\":false,\"host_stage_measurement\":true,\"promotion_blocked\":true,\"warmups\":3,\"repetitions\":"<<reps<<",\"real_capture_pairs\":"<<real_pairs<<",\"synthetic_duplicated_eye_cases\":"<<synthetic_cases<<",\"source_corpus_cases\":"<<source_cases<<",\"cpu_reference_and_gpu_bytes_exact\":true,\"poison_and_clear_in_total_time\":true,\"cases\":["<<std::flush;
    unsigned index=0;
    for(const auto& f:cases) {
        auto key=std::make_pair(f.width,f.height);auto& group=sessions[key];for(unsigned mode=0;mode<3;++mode)if(!group[mode])group[mode]=std::make_unique<Encoder>(f.width,f.height,argv[1],0,modes[mode]);
        auto& base=*group[0];validate_pair(f);
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(f.name)<<",\"synthetic_duplicated_eyes\":"<<(f.name.starts_with("corpus/synthetic/")?"true":"false")<<",\"device\":"<<quote(base.properties.deviceName)<<",\"device_type\":"<<base.properties.deviceType<<",\"driver\":"<<base.properties.driverVersion<<",\"width\":"<<f.width<<",\"height\":"<<f.height<<",\"eye_wire_bytes\":["<<f.reference[0].size()<<","<<f.reference[1].size()<<"],\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<reps+3;++rep)for(unsigned order=0;order<4;++order) {
            unsigned mode=(index+rep+order)%4;std::array<rgb_palette::Bytes,2> wire;std::array<Result,2> result;
            auto start=Clock::now();validate_pair(f);double preflight=elapsed(start),total=0;
            if(mode==3) { for(unsigned eye=0;eye<2;++eye)wire[eye]=rgb_palette::encode(f.pixels[eye],f.width,f.height);total=elapsed(start); }
            else { for(unsigned eye=0;eye<2;++eye)result[eye]=group[mode]->run(f.pixels[eye]);total=elapsed(start);for(unsigned eye=0;eye<2;++eye)wire[eye]=std::move(result[eye].wire); }
            // Every arm receives the same exact input pair. All allocation,
            // source validation, upload, poison, clear, submission, waits and
            // CRC assembly are inside total. Only this independent oracle and
            // reference construction, printing and one-time setup are outside.
            for(unsigned eye=0;eye<2;++eye) { require(wire[eye]==f.reference[eye],"timed canonical packet mismatch");require(rgb_palette::decode(wire[eye]).rgba==f.pixels[eye],"timed exact pixel mismatch"); }
            if(rep<3)continue;
            const char* name=mode==3?"cpu_canonical":mode_name(modes[mode]);
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-3<<",\"order\":"<<order<<",\"mode\":"<<quote(name)<<",\"stereo_total_ms\":"<<total<<",\"stereo_pair_preflight_ms\":"<<preflight;
            const std::array<std::pair<const char*,double>,5> components{{
                {"stereo_eye_preflight_ms",result[0].validation_ms+result[1].validation_ms},
                {"stereo_upload_ms",result[0].upload_ms+result[1].upload_ms},
                {"stereo_device_timestamp_span_ms",result[0].gpu_copy_ms+result[1].gpu_copy_ms},
                {"stereo_submit_wait_ms",result[0].submit_wait_ms+result[1].submit_wait_ms},
                {"stereo_assembly_crc_ms",result[0].assembly_crc_ms+result[1].assembly_crc_ms}}};
            for(auto [key,value]:components) { std::cout<<","<<quote(key)<<":";if(mode==3)std::cout<<"null";else std::cout<<value; }
            std::cout<<",\"stereo_copied_bytes\":"<<result[0].copied_bytes+result[1].copied_bytes<<",\"queue_submissions\":"<<result[0].queue_submissions+result[1].queue_submissions<<",\"canonical_bytes_exact\":true,\"decoded_pixels_exact\":true}";first=false;
        }
        std::cout<<"]}"<<std::flush;++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"scope\":\"Whole offline host encoding prototype on paired captured pixels; existing desktop workload remains uncontrolled; no network, headset or sub15 promotion\"}\n";return 0;
}catch(const std::exception& e) { std::cerr<<"palette-bench: "<<e.what()<<'\n';return 1; } }
