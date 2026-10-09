// SPDX-License-Identifier: MIT
#pragma once
#include "workers.hpp"
#include "../vr444/direct.hpp"

// An offline server-side transcode experiment. Input is existing VR444 wire;
// outputs preserve its decoded opaque RGB exactly, including existing Q8 loss.
namespace rowencoder {
using rowblocks::Bytes;using rowblocks::View;using rowblocks::need;using rowworkers::Pool;
struct Workspace {
    std::array<direct::Shape,2>shape{};
    std::array<std::vector<uint32_t>,2>rgba;
    std::array<std::vector<rowfilter::Workspace>,2>blocks;
    std::array<Bytes,2>wire;
    uint32_t block_rows=0;rowfilter::Policy policy=rowfilter::Policy::up;
    void clear(){for(auto&b:rgba)b.clear();for(auto&b:blocks)b.clear();for(auto&b:wire)b.clear();shape={};block_rows=0;}
};
inline bool alias(View input,const Workspace&w){
    auto a=reinterpret_cast<uintptr_t>(input.data()),b=reinterpret_cast<uintptr_t>(&w);
    if(!input.empty()&&(a<=b?b-a<input.size():a-b<sizeof(w)))return true;
    for(unsigned eye=0;eye<2;++eye){
        if(rowworkers::alias(input,w.rgba[eye])||rowworkers::alias(input,w.wire[eye])||rowworkers::alias(input,w.blocks[eye]))return true;
        for(const auto&block:w.blocks[eye])if(rowfilter::alias(input,block))return true;
    }return false;
}
struct Stereo {View left,right;uint32_t width,height,block_rows;};
struct Context {std::array<View,2>input;Workspace*workspace;};
inline void decode_eye(void*opaque,size_t eye,unsigned){
    auto&c=*static_cast<Context*>(opaque);auto&w=*c.workspace;need(eye<2);
    direct::decode(c.input[eye],w.shape[eye],w.rgba[eye],true);
}
inline void encode_block(void*opaque,size_t job,unsigned){
    auto&w=*static_cast<Workspace*>(opaque);size_t n=w.blocks[0].size();unsigned eye=job>=n;size_t local=job-(eye?n:0);
    need(eye<2&&local<w.blocks[eye].size());auto&s=w.shape[eye];size_t row=local*w.block_rows,h=std::min(size_t(w.block_rows),size_t(s.height)-row);
    auto pixels=std::span<const uint32_t>(w.rgba[eye]).subspan(row*s.width,h*s.width);
    rowfilter::encode(pixels,s.width,uint32_t(h),w.policy,w.blocks[eye][local],true);
}
inline void assemble(Workspace&w,unsigned eye){
    auto&s=w.shape[eye];auto&blocks=w.blocks[eye];auto&wire=w.wire[eye];size_t n=blocks.size(),table_end=rowblocks::header_bytes+n*rowblocks::entry_bytes,total=table_end;
    need(n>0&&n<=rowblocks::max_blocks);
    for(const auto&block:blocks){need(block.wire.size()>rowfilter::header_bytes&&block.wire.size()<=rowblocks::max_wire-total);total+=block.wire.size();}
    wire.reserve(total);wire.assign(table_end,0);
    for(size_t i=0;i<n;++i){rowblocks::put32(wire,rowblocks::header_bytes+i*8,uint32_t(wire.size()));rowblocks::put32(wire,rowblocks::header_bytes+i*8+4,uint32_t(blocks[i].wire.size()));wire.insert(wire.end(),blocks[i].wire.begin(),blocks[i].wire.end());}
    need(wire.size()==total&&wire.size()<=rowblocks::max_wire);
    wire[0]='R';wire[1]='R';wire[2]='B';wire[3]='1';wire[4]=1;wire[5]=uint8_t(rowblocks::row_shift(w.block_rows));
    rowblocks::put32(wire,8,s.width);rowblocks::put32(wire,12,s.height);rowblocks::put32(wire,16,uint32_t(n));rowblocks::put32(wire,20,uint32_t(total));
    rowblocks::put32(wire,24,wpc::crc(View(wire).subspan(rowblocks::header_bytes,n*8)));rowblocks::put32(wire,28,wpc::crc(View(wire).first(28)));
}
// The caller exclusively owns inputs/workspace/pool for this synchronous call.
// Two decoded-eye jobs finish before at most512 independent block jobs start.
// No encoded eye escapes until both final canonical frames are assembled. No
// frame queue, sender or publication is introduced. A returned lease expires on
// the next call/clear/destruction; retain a published frame in another workspace.
// Alias precondition failures preserve a previous lease. All later failures
// drain worker jobs, clear both staged outputs and invalidate partial candidates.
inline Stereo transcode_pair(View left,View right,uint32_t block_rows,rowfilter::Policy policy,Workspace&w,Pool*pool=nullptr){
    need(!alias(left,w)&&!alias(right,w));
    try{
        rowblocks::row_shift(block_rows);need(policy==rowfilter::Policy::plain||policy==rowfilter::Policy::up||policy==rowfilter::Policy::adaptive);
        w.shape={direct::preflight(left),direct::preflight(right)};
        need(w.shape[0].width==w.shape[1].width&&w.shape[0].height==w.shape[1].height);
        size_t pixels=rowfilter::pixels(w.shape[0].width,w.shape[0].height),n=(w.shape[0].height+block_rows-1)/block_rows;
        need(n<=rowblocks::max_blocks&&2*n<=rowworkers::max_jobs);w.block_rows=block_rows;w.policy=policy;
        for(unsigned eye=0;eye<2;++eye){w.rgba[eye].resize(pixels);w.blocks[eye].resize(n);}
        Context context{{left,right},&w};
        if(pool)pool->run(2,&context,decode_eye);else for(unsigned eye=0;eye<2;++eye)decode_eye(&context,eye,0);
        if(pool)pool->run(2*n,&w,encode_block);else for(size_t job=0;job<2*n;++job)encode_block(&w,job,0);
        assemble(w,0);assemble(w,1);return {w.wire[0],w.wire[1],w.shape[0].width,w.shape[0].height,block_rows};
    }catch(...){w.clear();throw;}
}
}
