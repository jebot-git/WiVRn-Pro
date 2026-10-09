// SPDX-License-Identifier: MIT
#pragma once
#include "../direct_image_gpu/gpu_oracle.hpp"
#include "../rgb_rowfilter/rowfilter.hpp"
#include <bit>
namespace rowfilter_gpu {
using namespace direct_image;
static_assert(std::endian::native==std::endian::little);
struct Fixture {
    std::string name;
    unsigned width,height;
    std::array<rowfilter::Bytes,2> wire;
    std::array<std::vector<uint32_t>,2> expected;
};
inline Measurement run_rowfilter(Session& session,const Fixture& fixture,unsigned mode,unsigned columns) {
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
inline void add_generated(std::vector<Fixture>& result,unsigned w,unsigned h,unsigned kind,unsigned q,rowfilter::Policy policy,const std::string& prefix="generated") {
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
inline std::vector<Fixture> generated(bool full,bool maximum) {
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
inline std::vector<uint8_t> read(const std::filesystem::path& path) {
    auto bytes=std::filesystem::file_size(path);require(bytes&&bytes<=16*1024*1024,"fixture file bound");
    std::vector<uint8_t> out(bytes);std::ifstream in(path,std::ios::binary);in.read(reinterpret_cast<char*>(out.data()),bytes);require(bool(in),"fixture read");return out;
}
inline void append_corpus(std::vector<Fixture>& cases,const std::filesystem::path& path,const std::string& label) {
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
} // namespace rowfilter_gpu
