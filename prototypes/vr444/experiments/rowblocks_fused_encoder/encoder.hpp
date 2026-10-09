// SPDX-License-Identifier: MIT
#pragma once
#include "../rowblocks_encoder_workers/encoder.hpp"
#include <type_traits>

// Fuses independent VR444 tile-band reconstruction with canonical RRF encoding.
// Only the source-frame headers are reframed in private scratch; tile records,
// quantizers and coefficients stay exact. RRB output and quality are unchanged.
namespace rowfused {
using rowencoder::Bytes;using rowencoder::View;using rowencoder::need;using rowencoder::Pool;using rowencoder::Stereo;
struct Scratch {Bytes source_band;std::vector<uint32_t>rgba;rowfilter::Workspace encoder;};
struct Workspace:rowencoder::Workspace {
    std::array<std::vector<size_t>,2>row_offsets;
    std::array<Scratch,4>scratch;
    void clear(){rowencoder::Workspace::clear();for(auto&v:row_offsets)v.clear();for(auto&s:scratch){s.source_band.clear();s.rgba.clear();s.encoder={};}}
};
inline bool alias(View input,const Workspace&w){
    auto a=reinterpret_cast<uintptr_t>(input.data()),b=reinterpret_cast<uintptr_t>(&w);
    if(!input.empty()&&(a<=b?b-a<input.size():a-b<sizeof(w)))return true;
    if(rowencoder::alias(input,w))return true;
    for(const auto&offsets:w.row_offsets)if(rowworkers::alias(input,offsets))return true;
    for(const auto&s:w.scratch)if(rowworkers::alias(input,s.source_band)||rowworkers::alias(input,s.rgba)||rowfilter::alias(input,s.encoder))return true;
    return false;
}
inline void index_rows(View wire,const direct::Shape&shape,std::vector<size_t>&offsets){
    size_t columns=(shape.width+15)/16,rows=(shape.height+15)/16,at=24;need(rows>0&&rows<=256&&columns>0&&columns<=256);
    offsets.resize(rows+1);offsets[0]=at;
    for(size_t row=0;row<rows;++row){
        for(size_t column=0;column<columns;++column){
            size_t header=shape.version==2?4:2;need(at<=wire.size()&&header<=wire.size()-at);
            size_t words=size_t(wire[at])|size_t(wire[at+1])<<8;need(words>=3&&words<=320);at+=header;need(words*4<=wire.size()-at);at+=words*4;
        }offsets[row+1]=at;
    }need(at==wire.size());
}
struct Context{std::array<View,2>source;Workspace*workspace;};
inline void fused_job(void*opaque,size_t job,unsigned worker){
    auto&c=*static_cast<Context*>(opaque);auto&w=*c.workspace;need(worker<w.scratch.size());
    size_t n=w.blocks[0].size();unsigned eye=job>=n;size_t local=job-(eye?n:0);need(eye<2&&local<w.blocks[eye].size());
    auto&shape=w.shape[eye];auto&scratch=w.scratch[worker];size_t row=local*w.block_rows,h=std::min(size_t(w.block_rows),size_t(shape.height)-row);
    need(row%16==0);size_t first=row/16,last=(row+h+15)/16;need(first<last&&last<w.row_offsets[eye].size());
    size_t start=w.row_offsets[eye][first],end=w.row_offsets[eye][last];need(start<=end&&end<=c.source[eye].size());
    scratch.source_band.assign(c.source[eye].begin(),c.source[eye].begin()+24);
    size_t tiles=((shape.width+15)/16)*((h+15)/16);need(tiles<=shape.tiles);
    rowblocks::put32(scratch.source_band,12,uint32_t(h));rowblocks::put32(scratch.source_band,20,uint32_t(tiles));
    scratch.source_band.insert(scratch.source_band.end(),c.source[eye].begin()+start,c.source[eye].begin()+end);
    scratch.rgba.resize(size_t(shape.width)*h);
    direct::Shape band_shape{shape.width,uint32_t(h),shape.q,uint32_t(tiles),shape.version};
    // direct::decode independently rechecks the reframed record grammar and all
    // coefficient bounds before returning the complete private band.
    direct::decode(scratch.source_band,band_shape,scratch.rgba,true);
    auto encoded=rowfilter::encode(scratch.rgba,shape.width,uint32_t(h),w.policy,scratch.encoder,true);
    w.blocks[eye][local].wire.assign(encoded.begin(),encoded.end());
}
// Same exclusive ownership, failure/alias and returned-lease contract as the
// two-stage encoder, but a single bounded pool barrier. Each worker reuses only
// its own band/decode/compression scratch. Completed per-block wire records are
// disjoint and stay private until both final canonical RRB frames are assembled.
inline Stereo transcode_pair(View left,View right,uint32_t block_rows,rowfilter::Policy policy,Workspace&w,Pool*pool=nullptr){
    need(!alias(left,w)&&!alias(right,w));
    try{
        rowblocks::row_shift(block_rows);need(policy==rowfilter::Policy::plain||policy==rowfilter::Policy::up||policy==rowfilter::Policy::adaptive);
        w.shape={direct::preflight(left),direct::preflight(right)};need(w.shape[0].width==w.shape[1].width&&w.shape[0].height==w.shape[1].height);
        rowfilter::pixels(w.shape[0].width,w.shape[0].height);size_t n=(w.shape[0].height+block_rows-1)/block_rows;need(n<=rowblocks::max_blocks&&2*n<=rowworkers::max_jobs);
        w.block_rows=block_rows;w.policy=policy;std::array<View,2>source{left,right};
        for(unsigned eye=0;eye<2;++eye){index_rows(source[eye],w.shape[eye],w.row_offsets[eye]);w.blocks[eye].resize(n);}
        Context context{source,&w};if(pool)pool->run(2*n,&context,fused_job);else for(size_t job=0;job<2*n;++job)fused_job(&context,job,0);
        rowencoder::assemble(w,0);rowencoder::assemble(w,1);return {w.wire[0],w.wire[1],w.shape[0].width,w.shape[0].height,block_rows};
    }catch(...){w.clear();throw;}
}
inline size_t retained_capacity_bytes(const Workspace&w){
    size_t total=0;auto bytes=[&](const auto&v){total+=v.capacity()*sizeof(typename std::decay_t<decltype(v)>::value_type);};
    auto filter=[&](const rowfilter::Workspace&f){bytes(f.rgb);bytes(f.filtered);bytes(f.compressed);bytes(f.alternate);bytes(f.wire);};
    for(unsigned e=0;e<2;++e){bytes(w.rgba[e]);bytes(w.blocks[e]);bytes(w.wire[e]);bytes(w.row_offsets[e]);for(const auto&block:w.blocks[e])filter(block);}
    for(const auto&s:w.scratch){bytes(s.source_band);bytes(s.rgba);filter(s.encoder);}return total;
}
}
