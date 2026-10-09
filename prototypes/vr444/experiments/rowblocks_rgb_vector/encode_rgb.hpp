// SPDX-License-Identifier: MIT
#pragma once
#include "../rgb_rowfilter/rowfilter.hpp"
namespace rgbvectorencode {
using rowfilter::Bytes;using rowfilter::View;using rowfilter::Policy;using rowfilter::Workspace;using rowfilter::need;
// Internal private-stage RGB24 source. There is no alpha channel to discard;
// each triple directly represents the opaque output of the checked decoder.
// Canonical policy/compression/header order is identical to rowfilter::encode.
// The source is immutable and separate from every workspace buffer.
inline View encode(View rgb,uint32_t width,uint32_t height,Policy policy,Workspace&w,bool simd=true){
    size_t count=rowfilter::pixels(width,height);need(rgb.size()==count*3&&!rowfilter::alias(rgb,w));
    need(policy==Policy::plain||policy==Policy::up||policy==Policy::adaptive);
    size_t bound=size_t(LZ4_compressBound(int(rgb.size())));w.compressed.resize(bound);
    View chosen=rgb;unsigned mode=0;
    auto attempt=[&](View input,Bytes&storage,unsigned candidate){
        storage.resize(bound);int n=LZ4_compress_default(reinterpret_cast<const char*>(input.data()),reinterpret_cast<char*>(storage.data()),int(input.size()),int(storage.size()));
        need(n>0);if(size_t(n)+64<=rgb.size()&&size_t(n)<chosen.size()){chosen=View(storage).first(size_t(n));mode=candidate;}
    };
    if(policy!=Policy::up)attempt(rgb,w.compressed,1);
    if(policy!=Policy::plain){rowfilter::predict(rgb,size_t(width)*3,w.filtered,simd);attempt(w.filtered,policy==Policy::up?w.compressed:w.alternate,2);}
    w.wire.assign(rowfilter::header_bytes,0);w.wire[0]='R';w.wire[1]='R';w.wire[2]='F';w.wire[3]='1';w.wire[4]=1;w.wire[5]=uint8_t(mode);
    rowfilter::put32(w.wire,8,width);rowfilter::put32(w.wire,12,height);rowfilter::put32(w.wire,16,uint32_t(chosen.size()));rowfilter::put32(w.wire,20,uint32_t(rgb.size()));
    rowfilter::put32(w.wire,24,wpc::crc(chosen));rowfilter::put32(w.wire,28,wpc::crc(View(w.wire).first(28)));
    w.wire.insert(w.wire.end(),chosen.begin(),chosen.end());return w.wire;
}
}
