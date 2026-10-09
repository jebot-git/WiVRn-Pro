// SPDX-License-Identifier: MIT
#pragma once
#include "../rowfilter_gpu/fixtures.hpp"
#include "../rgb_rowblocks/rowblocks.hpp"
namespace rowblocks_gpu {
using namespace direct_image;
using rowfilter_gpu::Fixture;
inline std::vector<Fixture> generated(bool full,bool maximum){
    auto originals=rowfilter_gpu::generated(full,maximum);std::vector<Fixture> cases;
    for(const auto& source:originals)for(unsigned rows:{16u,32u,64u,128u}){
        unsigned right=rows==128?16:rows*2;
        auto policy=source.name.ends_with("-policy1")?rowfilter::Policy::plain:rowfilter::Policy::up;
        Fixture f{source.name+"-leftrows"+std::to_string(rows)+"-rightrows"+std::to_string(right),source.width,source.height,{},source.expected};
        for(unsigned eye=0;eye<2;++eye){rowblocks::Workspace w;auto wire=rowblocks::encode(f.expected[eye],f.width,f.height,eye?right:rows,policy,w);f.wire[eye].assign(wire.begin(),wire.end());}
        cases.push_back(std::move(f));
    }
    return cases;
}
inline void append_corpus(std::vector<Fixture>& cases,const std::filesystem::path& path,const std::string& label){
    std::vector<std::filesystem::path> paths;
    for(auto& e:std::filesystem::recursive_directory_iterator(path))if(e.is_regular_file()&&e.path().extension()==".rrb")paths.push_back(e.path());
    std::sort(paths.begin(),paths.end());require(!paths.empty()&&paths.size()<=256,"RRB corpus count");size_t total=0;
    for(auto& item:paths){
        auto wire=rowfilter_gpu::read(item);auto index=rowblocks::inspect(wire);auto reference=item;reference.replace_extension(".rgba");
        if(!std::filesystem::exists(reference)){auto stem=item.stem().string(),suffix=".r"+std::to_string(index.block_rows);require(stem.ends_with(suffix),"RRB reference naming");reference=item.parent_path()/(stem.substr(0,stem.size()-suffix.size())+".rgba");}
        auto raw=rowfilter_gpu::read(reference);
        total+=wire.size()+raw.size();require(total<=512*1024*1024,"RRB corpus allocation bound");require(raw.size()==size_t(index.width)*index.height*4,"RRB reference shape");
        std::vector<uint32_t> pixels(raw.size()/4);std::memcpy(pixels.data(),raw.data(),raw.size());
        cases.push_back({label+"/"+std::filesystem::relative(item,path).string(),index.width,index.height,{wire,wire},{pixels,pixels}});
    }
}
struct Prepared {
    std::array<rowblocks::Workspace,2> work;
    std::array<rowblocks::Index,2> indices;
    unsigned groups{};
    Session* owner{};
    bool admitted=false;
    void admit(Session& s,const Fixture& fixture){
        admitted=false;owner=&s;
        require(fixture.width==s.width&&fixture.height==s.height,"RRB fixture shape");
        indices={rowblocks::unpack_rgb(fixture.wire[0],work[0]),rowblocks::unpack_rgb(fixture.wire[1],work[1])};
        size_t count=0;
        for(unsigned eye=0;eye<2;++eye){
            const auto& index=indices[eye];require(index.width==s.width&&index.height==s.height,"RRB stereo dimensions");
            require(!index.blocks.empty()&&index.blocks.size()<=(s.height+15)/16,"RRB block domain");count=std::max(count,index.blocks.size());
            require(((work[eye].rgb.size()+3)&~size_t(3))<=s.o.inputs[eye][0].bytes,"RRB residual allocation");
            require((1+index.blocks.size()*4)*4<=s.o.inputs[eye][1].bytes,"RRB metadata allocation");
        }
        groups=unsigned(((s.width+127)/128)*count);require(groups<=s.properties.limits.maxComputeWorkGroupCount[0],"RRB dispatch limit");
        admitted=true;
    }
    void upload(Session& s)const{
        require(admitted&&owner==&s,"RRB upload requires complete admission for this session");
        for(unsigned eye=0;eye<2;++eye){
            const auto& rgb=work[eye].rgb;auto& input=s.o.inputs[eye];std::memcpy(input[0].mapped,rgb.data(),rgb.size());
            std::memset(static_cast<uint8_t*>(input[0].mapped)+rgb.size(),0,((rgb.size()+3)&~size_t(3))-rgb.size());
            auto* meta=static_cast<uint32_t*>(input[1].mapped);meta[0]=indices[eye].blocks.size();
            for(size_t b=0;b<indices[eye].blocks.size();++b){const auto& block=indices[eye].blocks[b];
                meta[1+b*4]=block.row;meta[2+b*4]=block.height;meta[3+b*4]=block.mode;meta[4+b*4]=uint32_t(block.raw_offset);
            }
            *static_cast<uint32_t*>(input[2].mapped)=rgb.size();
        }
    }
};
inline Measurement run(Session& s,const Fixture& fixture,unsigned mode){
    require(mode<modes,"RRB output arm");auto start=Clock::now();Prepared prepared;prepared.admit(s,fixture);
    Measurement result;result.validation_ms=elapsed(start);s.poison(mode);start=Clock::now();prepared.upload(s);result.input_copy_ms=elapsed(start);
    return s.execute(mode,fixture.expected,result,prepared.groups);
}
} // namespace rowblocks_gpu
