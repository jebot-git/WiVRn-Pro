// SPDX-License-Identifier: MIT
#pragma once
#include "../rgb_rowblocks/rowblocks.hpp"
namespace mapped_input {
using rowfilter::View;
using Mutable=std::span<uint8_t>;
using Decoder=int(*)(const char*,char*,int,int);
// A synchronous immutable-wire lease. The caller retains both wire allocations
// and both distinct destinations until this object is no longer used. It must
// not mutate either wire after construction. No copy of compressed input is
// hidden outside the measured path; the fixture owners supply that lifetime.
class Pair {
    bool blocks_,started_=false,complete_=false;
    std::array<View,2> wires_;
    std::array<Mutable,2> destinations_;
    std::array<rowfilter::Shape,2> shapes_{};
    std::array<rowblocks::Index,2> indices_{};
public:
    Pair(const Pair&)=delete;
    Pair& operator=(const Pair&)=delete;
    Pair(bool blocks,unsigned width,unsigned height,std::array<View,2> wires,std::array<Mutable,2> destinations)
        :blocks_(blocks),wires_(wires),destinations_(destinations){
        const size_t bytes=rowfilter::pixels(width,height)*3;
        // Every capacity and cross-eye alias precondition precedes output writes.
        for(unsigned eye=0;eye<2;++eye){
            rowfilter::need(destinations_[eye].size()>=((bytes+3)&~size_t(3)));
            for(unsigned source=0;source<2;++source)rowfilter::need(!wpc::overlaps(wires_[source],destinations_[eye]));
        }
        rowfilter::need(!wpc::overlaps(destinations_[0],destinations_[1]));
        // Both complete CRC/grammar inspections finish before callers can poison,
        // decompress into mapped storage, or issue any readiness submission.
        for(unsigned eye=0;eye<2;++eye){
            if(blocks_){indices_[eye]=rowblocks::inspect(wires_[eye]);const auto& ix=indices_[eye];
                rowfilter::need(ix.width==width&&ix.height==height);shapes_[eye]={width,height,0,uint32_t(bytes)};
            }else{shapes_[eye]=rowfilter::inspect(wires_[eye]);rowfilter::need(shapes_[eye].width==width&&shapes_[eye].height==height);}
            rowfilter::need(shapes_[eye].raw_bytes==bytes);
        }
    }
    bool complete()const{return complete_;}
    const rowfilter::Shape& shape(unsigned eye)const{rowfilter::need(eye<2);return shapes_[eye];}
    const rowblocks::Index& index(unsigned eye)const{rowfilter::need(blocks_&&eye<2);return indices_[eye];}
    void decode(Decoder decoder=LZ4_decompress_safe){
        bool first=!started_;started_=true;complete_=false;rowfilter::need(first&&decoder);
        auto expand=[&](View body,Mutable out,unsigned mode){
            rowfilter::need(body.size()<=rowfilter::max_body&&out.size()<=rowfilter::max_body);
            if(mode){int n=decoder(reinterpret_cast<const char*>(body.data()),reinterpret_cast<char*>(out.data()),int(body.size()),int(out.size()));rowfilter::need(n==int(out.size()));}
            else{rowfilter::need(body.size()==out.size());std::copy(body.begin(),body.end(),out.begin());}
        };
        for(unsigned eye=0;eye<2;++eye){
            if(blocks_)for(const auto& block:indices_[eye].blocks)
                expand(wires_[eye].subspan(block.offset+rowfilter::header_bytes,block.length-rowfilter::header_bytes),destinations_[eye].subspan(block.raw_offset,block.raw_bytes),block.mode);
            else expand(wires_[eye].subspan(rowfilter::header_bytes),destinations_[eye].first(shapes_[eye].raw_bytes),shapes_[eye].mode);
            size_t bytes=shapes_[eye].raw_bytes;std::fill(destinations_[eye].begin()+bytes,destinations_[eye].begin()+((bytes+3)&~size_t(3)),0);
        }
        // Any exceptional decoder result leaves this false, even if the first
        // destination was already written. Such data must never be submitted.
        complete_=true;
    }
};
}
