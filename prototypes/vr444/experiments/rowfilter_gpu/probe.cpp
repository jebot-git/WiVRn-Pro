// SPDX-License-Identifier: MIT
#include "../direct_image_gpu/gpu_oracle.hpp"
#include "../rgb_rowfilter/rowfilter.hpp"
#include <bit>
using namespace direct_image;
static_assert(std::endian::native==std::endian::little);
struct Fixture {
    std::string name;
    unsigned width,height;
    std::array<rowfilter::Bytes,2> wire;
    std::array<std::vector<uint32_t>,2> expected;
};
Measurement run_rowfilter(Session& session,const Fixture& fixture,unsigned mode,unsigned columns) {
    require(mode<modes&&(columns==1||columns==4||columns==16),"rowfilter dispatch choice");
    const unsigned groups=(session.width+columns-1)/columns;
    require(groups<=session.properties.limits.maxComputeWorkGroupCount[0],"rowfilter dispatch capability");
    Measurement m;auto start=Clock::now();
    std::array<rowfilter::Workspace,2> work;
    // Neither eye reaches mapped storage, poison or submission until both
    // immutable packets have passed complete CRC/grammar/LZ4 reconstruction.
    std::array<rowfilter::Shape,2> shapes{
        rowfilter::unpack_rgb(fixture.wire[0],work[0]),
        rowfilter::unpack_rgb(fixture.wire[1],work[1])};
    for(unsigned eye=0;eye<2;++eye) {
        require(shapes[eye].width==session.width&&shapes[eye].height==session.height,"RRF stereo shape");
        require(((shapes[eye].raw_bytes+3)&~size_t(3))<=session.o.inputs[eye][0].bytes,"RRF raw allocation");
    }
    m.validation_ms=elapsed(start);session.poison(mode);start=Clock::now();
    for(unsigned eye=0;eye<2;++eye) {
        auto& b=session.o.inputs[eye];const auto& rgb=work[eye].rgb;
        std::memcpy(b[0].mapped,rgb.data(),rgb.size());
        std::memset(static_cast<uint8_t*>(b[0].mapped)+rgb.size(),0,((rgb.size()+3)&~size_t(3))-rgb.size());
        *static_cast<uint32_t*>(b[1].mapped)=shapes[eye].mode;
        *static_cast<uint32_t*>(b[2].mapped)=shapes[eye].raw_bytes;
    }
    m.input_copy_ms=elapsed(start);
    return session.execute(mode,fixture.expected,m,groups);
}
void add_generated(std::vector<Fixture>& result,unsigned w,unsigned h,unsigned kind,unsigned q,rowfilter::Policy policy,const std::string& prefix="generated") {
    Fixture f{prefix+"/"+std::to_string(w)+"x"+std::to_string(h)+"-p"+std::to_string(kind)+"-q"+std::to_string(q)+"-policy"+std::to_string(unsigned(policy)),w,h,{},{}};
    for(unsigned eye=0;eye<2;++eye) {
        auto& pixels=f.expected[eye];pixels.resize(size_t(w)*h);uint32_t seed=0x125687+eye;
        for(unsigned y=0;y<h;++y)for(unsigned x=0;x<w;++x) {
            seed=seed*1664525u+1013904223u;
            uint32_t p=(x*3+y*29+eye*71)&255;
            p|=((x*17+y*255+eye*39)&255)<<8;p|=((x*7+y*127+eye*131)&255)<<16;
            if(kind==0)p=0x832719+eye*0x17853;
            if(kind==1)p=seed>>8;
            pixels[size_t(y)*w+x]=p|0xff000000u;
        }
        // Preserve the exact output of the existing Q1/Q8 codec; RRF itself
        // neither quantizes nor changes those pixels.
        pixels=vr444::decode(vr444::encode(pixels,w,h,q));
        rowfilter::Workspace workspace;auto wire=rowfilter::encode(pixels,w,h,policy,workspace);
        f.wire[eye].assign(wire.begin(),wire.end());
    }
    result.push_back(std::move(f));
}
std::vector<Fixture> generated(bool full,bool maximum) {
    std::vector<Fixture> result;
    std::vector<std::pair<unsigned,unsigned>> dimensions{{1,1},{1,17},{17,1},{17,19},{31,33},{65,47},{256,2}};
    if(full)dimensions.emplace_back(576,640);
    for(auto [w,h]:dimensions)for(unsigned kind=0;kind<3;++kind)for(unsigned q:{1u,8u})
        for(auto policy:{rowfilter::Policy::plain,rowfilter::Policy::up})add_generated(result,w,h,kind,q,policy);
    // Exact segment boundaries, empty segments and modulo-256 wrap at tall
    // dimensions. The three column layouts have 128, 32 and 8 segments.
    for(unsigned h:{7u,8u,9u,31u,32u,33u,127u,128u,129u,255u,256u,257u,4095u,4096u})
        for(unsigned w:{1u,17u})add_generated(result,w,h,2,1,rowfilter::Policy::up,"boundary");
    add_generated(result,4096,1,2,1,rowfilter::Policy::up,"boundary");
    add_generated(result,4095,3,2,1,rowfilter::Policy::up,"boundary");
    if(maximum)for(unsigned kind:{0u,1u})add_generated(result,2048,2048,kind,1,rowfilter::Policy::up,"maximum");
    return result;
}
std::vector<uint8_t> read(const std::filesystem::path& path) {
    auto bytes=std::filesystem::file_size(path);require(bytes&&bytes<=16*1024*1024,"fixture file bound");
    std::vector<uint8_t> out(bytes);std::ifstream in(path,std::ios::binary);in.read(reinterpret_cast<char*>(out.data()),bytes);require(bool(in),"fixture read");return out;
}
void append_corpus(std::vector<Fixture>& cases,const std::filesystem::path& path,const std::string& label) {
    std::vector<std::filesystem::path> paths;
    for(auto& e:std::filesystem::recursive_directory_iterator(path))if(e.is_regular_file()&&e.path().extension()==".rrf")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());require(!paths.empty()&&paths.size()<=128,"corpus count");size_t total=0;
    for(auto& item:paths) {
        auto wire=read(item);auto shape=rowfilter::inspect(wire);auto reference=item;reference.replace_extension(".rgba");auto raw=read(reference);
        total+=wire.size()+raw.size();require(total<=128*1024*1024,"corpus allocation bound");require(raw.size()==shape.raw_bytes/3*4,"reference shape");
        std::vector<uint32_t> pixels(raw.size()/4);std::memcpy(pixels.data(),raw.data(),raw.size());
        cases.push_back({label+"/"+std::filesystem::relative(item,path).string(),shape.width,shape.height,{wire,wire},{pixels,pixels}});
    }
}
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
