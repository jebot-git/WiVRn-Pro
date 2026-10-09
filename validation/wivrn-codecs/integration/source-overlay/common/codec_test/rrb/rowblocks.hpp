// SPDX-License-Identifier: MIT
#pragma once
#include "rowfilter.hpp"

// RRB1: independent bounded RRF1 row blocks, with canonical indexing. This is
// an offline prototype and neither a streaming transport nor a stereo publisher.
namespace rowblocks {
using rowfilter::Bytes;using rowfilter::View;using rowfilter::need;
using rowfilter::bytes;using rowfilter::put32;using rowfilter::Policy;
constexpr size_t header_bytes=32,entry_bytes=8,max_blocks=256;
constexpr size_t max_wire=rowfilter::max_body+header_bytes+max_blocks*(entry_bytes+rowfilter::header_bytes);
struct Block {uint32_t row,height,mode;size_t offset,length,raw_offset,raw_bytes;};
struct Index {uint32_t width,height,block_rows;std::vector<Block> blocks;};
struct Workspace {rowfilter::Workspace block;Bytes wire,rgb;};
inline bool alias(View input,const Workspace&w){return rowfilter::alias(input,w.block)||rowfilter::alias(input,w.wire)||rowfilter::alias(input,w.rgb);}
inline unsigned row_shift(uint32_t rows){
    for(unsigned shift=4;shift<=7;++shift)if(rows==(1u<<shift))return shift;
    need(false);return 0;
}
inline View encode(std::span<const uint32_t>rgba,uint32_t width,uint32_t height,
                   uint32_t block_rows,Policy policy,Workspace&w,bool simd=true){
    auto count=rowfilter::pixels(width,height);need(rgba.size()==count&&!alias(bytes(rgba),w));
    unsigned shift=row_shift(block_rows);size_t n=(height+block_rows-1)/block_rows;
    need(n<=max_blocks);w.wire.assign(header_bytes+entry_bytes*n,0);
    for(size_t i=0,row=0;i<n;++i,row+=block_rows){
        size_t h=std::min(size_t(block_rows),size_t(height)-row);
        auto b=rowfilter::encode(rgba.subspan(row*width,h*width),width,uint32_t(h),policy,w.block,simd);
        put32(w.wire,header_bytes+entry_bytes*i,uint32_t(w.wire.size()));
        put32(w.wire,header_bytes+entry_bytes*i+4,uint32_t(b.size()));
        w.wire.insert(w.wire.end(),b.begin(),b.end());
    }
    need(w.wire.size()<=max_wire);
    w.wire[0]='R';w.wire[1]='R';w.wire[2]='B';w.wire[3]='1';w.wire[4]=1;w.wire[5]=uint8_t(shift);
    put32(w.wire,8,width);put32(w.wire,12,height);put32(w.wire,16,uint32_t(n));put32(w.wire,20,uint32_t(w.wire.size()));
    put32(w.wire,24,wpc::crc(View(w.wire).subspan(header_bytes,entry_bytes*n)));
    put32(w.wire,28,wpc::crc(View(w.wire).first(28)));return w.wire;
}
// The wire must remain immutable while this index or decoded residuals are used.
// Every block is checked before returning; offsets refer to complete RRF1 frames.
inline Index inspect(View wire){
    need(wire.size()>header_bytes&&wire.size()<=max_wire);
    need(wire[0]=='R'&&wire[1]=='R'&&wire[2]=='B'&&wire[3]=='1'&&wire[4]==1&&
         wire[5]>=4&&wire[5]<=7&&wire[6]==0&&wire[7]==0);
    Index index{wpc::u32(wire,8),wpc::u32(wire,12),1u<<wire[5],{}};
    rowfilter::pixels(index.width,index.height);
    size_t n=(index.height+index.block_rows-1)/index.block_rows;
    need(n<=max_blocks&&wpc::u32(wire,16)==n&&wpc::u32(wire,20)==wire.size());
    size_t cursor=header_bytes+n*entry_bytes;need(cursor<wire.size());
    need(wpc::u32(wire,28)==wpc::crc(wire.first(28))&&
         wpc::u32(wire,24)==wpc::crc(wire.subspan(header_bytes,n*entry_bytes)));
    index.blocks.reserve(n);
    for(size_t i=0,row=0;i<n;++i,row+=index.block_rows){
        size_t offset=wpc::u32(wire,header_bytes+i*entry_bytes),length=wpc::u32(wire,header_bytes+i*entry_bytes+4);
        need(offset==cursor&&length<=wire.size()-cursor);
        auto shape=rowfilter::inspect(wire.subspan(cursor,length));
        auto h=std::min(index.block_rows,index.height-uint32_t(row));
        need(shape.width==index.width&&shape.height==h);
        index.blocks.push_back({uint32_t(row),h,shape.mode,offset,length,row*index.width*3,shape.raw_bytes});cursor+=length;
    }
    need(cursor==wire.size());return index;
}
// All CRC/grammar checks and all decompressions complete before the residual
// buffer becomes eligible for a downstream consumer. Malformed nonaliasing input
// clears it. The alias precondition is checked before changing workspace state.
inline Index unpack_rgb(View wire,Workspace&w){
    need(!alias(wire,w));w.rgb.clear();
    try{auto index=inspect(wire);w.rgb.resize(size_t(index.width)*index.height*3);
        for(auto&b:index.blocks){
            auto body=wire.subspan(b.offset+rowfilter::header_bytes,b.length-rowfilter::header_bytes);
            auto dest=std::span(w.rgb).subspan(b.raw_offset,b.raw_bytes);
            if(b.mode){int n=LZ4_decompress_safe(reinterpret_cast<const char*>(body.data()),reinterpret_cast<char*>(dest.data()),int(body.size()),int(dest.size()));need(n==int(dest.size()));}
            else std::copy(body.begin(),body.end(),dest.begin());
        }return index;
    }catch(...){w.rgb.clear();throw;}
}
inline Index decode_into(View wire,std::span<uint32_t>output,Workspace&w,bool simd=true){
    need(output.size()<=rowfilter::max_pixels&&!wpc::overlaps(wire,bytes(output))&&!alias(bytes(output),w));
    try{auto index=unpack_rgb(wire,w);need(output.size()==size_t(index.width)*index.height);
        for(auto&b:index.blocks){
            if(b.mode==2){size_t stride=size_t(index.width)*3;
                for(size_t row=stride;row<b.raw_bytes;row+=stride)
                    rowfilter::detail::add_row(std::span(w.rgb).subspan(b.raw_offset+row,stride),View(w.rgb).subspan(b.raw_offset+row-stride,stride),simd);
            }
        }
        rowfilter::detail::expand(w.rgb,output,simd);return index;
    }catch(...){w.rgb.clear();throw;}
}
}
