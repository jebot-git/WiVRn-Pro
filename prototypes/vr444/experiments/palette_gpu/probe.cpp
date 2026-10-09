// SPDX-License-Identifier: MIT
#include "../direct_image_gpu/gpu_oracle.hpp"
#include "rgb_palette.hpp"
#include <bit>
#include <span>
using namespace direct_image;
static_assert(sizeof(rgb_palette::TileRecord)==10*sizeof(uint32_t));
static_assert(alignof(rgb_palette::TileRecord)==alignof(uint32_t));
static_assert(std::endian::native==std::endian::little);
struct Fixture {
    std::string name;
    std::array<rgb_palette::Bytes,2> wire;
    std::array<std::vector<uint32_t>,2> expected;
};
Measurement run_palette(Session& session,const Fixture& fixture,unsigned mode) {
    Measurement m; auto start=Clock::now();
    // Each fixture owns immutable bytes throughout preflight, upload, fence and
    // oracle. Both eyes validate before any image poison, mapped write or submit.
    std::array<rgb_palette::Layout,2> layouts{rgb_palette::inspect(fixture.wire[0]),rgb_palette::inspect(fixture.wire[1])};
    for(unsigned eye=0;eye<2;++eye) {
        const auto& layout=layouts[eye]; const auto& buffers=session.o.inputs[eye];
        require(layout.width==session.width&&layout.height==session.height&&layout.tiles.size()==session.tiles,"RPT frame shape");
        require(layout.tiles.size()*sizeof(rgb_palette::TileRecord)==buffers[1].bytes,"RPT index allocation");
        require(((fixture.wire[eye].size()+3)&~size_t(3))<=buffers[0].bytes,"RPT packet allocation");
    }
    m.validation_ms=elapsed(start); session.poison(mode); start=Clock::now();
    for(unsigned eye=0;eye<2;++eye) {
        const auto& wire=fixture.wire[eye]; auto& buffers=session.o.inputs[eye];
        std::memcpy(buffers[0].mapped,wire.data(),wire.size());
        auto padding=((wire.size()+3)&~size_t(3))-wire.size();
        std::memset(static_cast<uint8_t*>(buffers[0].mapped)+wire.size(),0,padding);
        std::memcpy(buffers[1].mapped,layouts[eye].tiles.data(),buffers[1].bytes);
        *static_cast<uint32_t*>(buffers[2].mapped)=wire.size();
    }
    m.input_copy_ms=elapsed(start);
    return session.execute(mode,fixture.expected,m);
}
std::vector<Fixture> generated(bool full) {
    std::vector<Fixture> result;
    std::vector<std::pair<unsigned,unsigned>> dimensions{{1,1},{1,17},{17,1},{17,19},{31,33},{65,47},{256,2}};
    if(full) dimensions.emplace_back(576,640);
    for(auto [w,h]:dimensions) for(unsigned kind=0;kind<6;++kind) for(unsigned q:{1u,8u}) {
        Fixture fixture; fixture.name=std::to_string(w)+"x"+std::to_string(h)+"-pattern"+std::to_string(kind)+"-q"+std::to_string(q);
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
            auto vr=vr444::encode(pixels,w,h,q,qs);
            fixture.expected[eye]=vr444::decode(vr);
            fixture.wire[eye]=rgb_palette::encode(fixture.expected[eye],w,h);
            require(rgb_palette::decode(fixture.wire[eye]).rgba==fixture.expected[eye],"RPT changed reference VR444 pixels");
        }
        result.push_back(std::move(fixture));
    }
    return result;
}
std::vector<uint8_t> read(const std::filesystem::path& path) {
    auto size=std::filesystem::file_size(path); require(size&&size<=16*1024*1024,"input file bound");
    std::vector<uint8_t> result(size); std::ifstream file(path,std::ios::binary); file.read(reinterpret_cast<char*>(result.data()),size); require(bool(file),"input read"); return result;
}
void append_corpus(std::vector<Fixture>& fixtures,const std::filesystem::path& corpus,const std::string& label) {
    std::vector<std::filesystem::path> paths;
    for(const auto& e:std::filesystem::recursive_directory_iterator(corpus)) if(e.is_regular_file()&&e.path().extension()==".rpt") paths.push_back(e.path());
    std::sort(paths.begin(),paths.end()); require(paths.size()<=128,"corpus case bound"); size_t total=0;
    for(const auto& path:paths) {
        auto wire=read(path); auto reference=path; reference.replace_extension(".rgba"); auto bytes=read(reference);
        total+=wire.size()+bytes.size(); require(total<=128*1024*1024,"corpus allocation bound");
        auto layout=rgb_palette::inspect(wire); require(bytes.size()==size_t(layout.width)*layout.height*4,"RGBA reference dimensions");
        std::vector<uint32_t> pixels(bytes.size()/4);
        for(size_t i=0;i<pixels.size();++i) for(unsigned c=0;c<4;++c) pixels[i]|=uint32_t(bytes[i*4+c])<<(c*8);
        require(rgb_palette::decode(wire).rgba==pixels,"imported RPT golden/reference differs from CPU decode");
        fixtures.push_back({label+"/"+std::filesystem::relative(path,corpus).string(),{wire,wire},{pixels,pixels}});
    }
}
int main(int argc,char** argv) { try {
    require(argc>=3,"usage: palette-gpu SHADERS GOLDEN_DIR [--corpus DIR] [--extra-goldens DIR] [--full] [--reps N] [--device N] [--format-list] [--cpu-only]");
    bool full=false,format_list=false,cpu_only=false; unsigned reps=1,selected=0; std::filesystem::path corpus,extra;
    for(int i=3;i<argc;++i) {
        std::string arg=argv[i];
        if(arg=="--full") full=true;
        else if(arg=="--format-list") format_list=true;
        else if(arg=="--cpu-only") cpu_only=true;
        else { require(i+1<argc,"missing argument value"); std::string value=argv[++i];
            if(arg=="--corpus") corpus=value;
            else if(arg=="--extra-goldens") extra=value;
            else if(arg=="--reps"||arg=="--device") { size_t end=0; auto number=std::stoul(value,&end); require(end==value.size()&&number<=30,"numeric argument bound"); if(arg=="--reps") reps=number; else selected=number; }
            else throw std::runtime_error("unknown option "+arg);
        }
    }
    require(reps>=1,"repetitions");
    auto cases=generated(full); append_corpus(cases,argv[2],"golden");
    if(!corpus.empty()) append_corpus(cases,corpus,"captured");
    if(!extra.empty()) append_corpus(cases,extra,"extra-golden");
    std::array<uint64_t,6> mode_counts{};
    for(const auto& fixture:cases) for(unsigned eye=0;eye<2;++eye) {
        auto layout=rgb_palette::inspect(fixture.wire[eye]);
        for(auto& tile:layout.tiles) { require(tile.mode<6,"mode bounds"); ++mode_counts[tile.mode]; }
        require(rgb_palette::decode(fixture.wire[eye]).rgba==fixture.expected[eye],"CPU fixture mismatch");
        auto bad=fixture.wire[eye]; bad.back()^=1; bool rejected=false;
        try { rgb_palette::inspect(bad); } catch(const std::exception&) { rejected=true; }
        require(rejected,"changed packet accepted");
    }
    for(auto count:mode_counts) require(count>0,"all six RPT modes must be covered");
    if(cpu_only) { std::cout<<"{\"cpu_fixture_pass\":true,\"fixture_count\":"<<cases.size()<<",\"rpt_modes\":6,\"vulkan_initialized\":false,\"headset_used\":false}\n"; return 0; }
    std::map<std::pair<unsigned,unsigned>,std::unique_ptr<Session>> sessions;
    std::cout<<"{\"schema\":1,\"experiment\":\"palette-gpu\",\"headset_used\":false,\"format_list\":"<<(format_list?"true":"false")<<",\"cpu_reconstruction_in_timed_path\":false,\"rpt_modes\":6,\"warmups\":1,\"repetitions\":"<<reps<<",\"cases\":["<<std::flush;
    unsigned index=0;
    for(const auto& fixture:cases) {
        auto layout=rgb_palette::inspect(fixture.wire[0]); auto key=std::make_pair(layout.width,layout.height);
        if(!sessions[key]) sessions[key]=std::make_unique<Session>(key.first,key.second,argv[1],selected,false,format_list,10);
        auto& session=*sessions[key]; std::array<std::vector<float>,2> baseline;
        auto bad=fixture; bad.wire[1].back()^=1; unsigned before=session.submissions; bool rejected=false;
        try { run_palette(session,bad,0); } catch(const std::exception&) { rejected=true; }
        require(rejected&&session.submissions==before,"malformed stereo RPT reached submission");
        std::cout<<(index?",":"")<<"{\"name\":"<<quote(fixture.name)<<",\"device\":"<<quote(session.properties.deviceName)<<",\"device_type\":"<<session.properties.deviceType<<",\"driver\":"<<session.properties.driverVersion<<",\"input_memory_type\":"<<session.o.inputs[0][0].type<<",\"input_memory_flags\":"<<session.o.inputs[0][0].flags<<",\"width\":"<<key.first<<",\"height\":"<<key.second<<",\"stereo_rpt_bytes\":"<<fixture.wire[0].size()+fixture.wire[1].size()<<",\"stereo_index_bytes\":"<<session.tiles*40*2<<",\"iterations\":[";
        bool first=true;
        for(unsigned rep=0;rep<reps+1;++rep) for(unsigned step=0;step<modes;++step) {
            unsigned mode=rep==0?step:(index+rep+step)%modes;
            auto m=run_palette(session,fixture,mode);
            for(unsigned eye=0;eye<2;++eye) {
                auto* samples=static_cast<float*>(session.o.linear[eye].mapped);
                if(rep==0&&mode==0) baseline[eye].assign(samples,samples+size_t(key.first)*key.second*4);
                else require(std::memcmp(samples,baseline[eye].data(),session.o.linear[eye].bytes)==0,"RPT image samples differ from buffer-copy SRGB baseline");
            }
            if(!rep) continue;
            std::cout<<(first?"":",")<<"{\"rep\":"<<rep-1<<",\"order\":"<<step<<",\"mode\":"<<quote(names[mode])<<",\"validation_ms\":"<<m.validation_ms<<",\"input_copy_ms\":"<<m.input_copy_ms<<",\"stereo_gpu_ms\":"<<m.gpu_ms<<",\"pipeline_wall_ms\":"<<m.pipeline_wall_ms<<",\"oracle_ms\":"<<m.oracle_ms<<",\"max_linear_error\":"<<m.max_linear_error<<",\"max_srgb_code_error\":"<<m.max_srgb_code_error<<",\"bytes_exact\":true,\"one_eotf\":true,\"sampled_baseline_exact\":true,\"malformed_pair_rejected_before_submit\":true}";
            first=false;
        }
        std::cout<<"]}"<<std::flush; ++index;
    }
    std::cout<<"],\"case_count\":"<<index<<",\"pass\":true,\"promotion_blocked\":true,\"scope\":\"RPT1 complete CRC/grammar preflight plus GPU expansion; exact decoded VR444 pixels; no XR, network, headset or performance promotion\"}\n";
    return 0;
} catch(const std::exception& e) { std::cerr<<"palette-gpu: "<<e.what()<<'\n'; return 1; } }
