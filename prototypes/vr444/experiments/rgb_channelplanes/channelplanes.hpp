// SPDX-License-Identifier: MIT
#pragma once
#include "../rgb_rowfilter/rowfilter.hpp"

// RCP1 is a separate exact opaque-RGB layout experiment, not production VR444.
namespace channelplanes {
using rowfilter::Bytes;using rowfilter::View;using rowfilter::need;using rowfilter::bytes;using rowfilter::put32;
constexpr size_t header_bytes=32,max_wire=rowfilter::max_wire;
enum class Layout:unsigned {rgb=0,planes=1,green_delta=2,green_delta_planes=3};
struct Workspace {Bytes rgb,ordered,filtered,compressed,wire,decoded;};
inline bool alias(View input,const Workspace&w){
    return rowfilter::alias(input,w.rgb)||rowfilter::alias(input,w.ordered)||rowfilter::alias(input,w.filtered)||
           rowfilter::alias(input,w.compressed)||rowfilter::alias(input,w.wire)||rowfilter::alias(input,w.decoded);
}
// Layout2/3 stores (G, R-G, B-G) modulo256, an exact bijection. Layout1/3
// stores each of those three channels as a separate contiguous plane.
inline void order(View rgb,std::span<uint8_t>output,Layout layout){
    need(rgb.size()==output.size()&&rgb.size()%3==0&&unsigned(layout)<=3&&!wpc::overlaps(rgb,View(output)));
    size_t pixels=rgb.size()/3;bool planar=unsigned(layout)&1,delta=unsigned(layout)&2;
    for(size_t i=0;i<pixels;++i){uint8_t r=rgb[3*i],g=rgb[3*i+1],b=rgb[3*i+2];
        uint8_t a=delta?g:r,c=delta?uint8_t(r-g):g,d=delta?uint8_t(b-g):b;
        if(planar){output[i]=a;output[pixels+i]=c;output[2*pixels+i]=d;}
        else{output[3*i]=a;output[3*i+1]=c;output[3*i+2]=d;}
    }
}
inline void unorder(View ordered,std::span<uint8_t>rgb,Layout layout){
    need(ordered.size()==rgb.size()&&rgb.size()%3==0&&unsigned(layout)<=3&&!wpc::overlaps(ordered,View(rgb)));
    size_t pixels=rgb.size()/3;bool planar=unsigned(layout)&1,delta=unsigned(layout)&2;
    for(size_t i=0;i<pixels;++i){uint8_t a=ordered[planar?i:3*i],b=ordered[planar?pixels+i:3*i+1],c=ordered[planar?2*pixels+i:3*i+2];
        rgb[3*i]=delta?uint8_t(b+a):a;rgb[3*i+1]=delta?a:b;rgb[3*i+2]=delta?uint8_t(c+a):c;
    }
}
inline void predict(View input,uint32_t width,uint32_t height,Layout layout,Bytes&output){
    size_t pixels=rowfilter::pixels(width,height);need(input.size()==pixels*3&&unsigned(layout)<=3&&!rowfilter::alias(input,output));output.resize(input.size());
    bool planar=unsigned(layout)&1;size_t domains=planar?3:1,length=planar?pixels:pixels*3,stride=size_t(width)*(planar?1:3);
    for(size_t p=0;p<domains;++p){auto in=input.subspan(p*length,length);auto out=std::span(output).subspan(p*length,length);
        std::copy_n(in.data(),stride,out.data());
        rowfilter::detail::subtract(in.subspan(stride),in.first(length-stride),out.subspan(stride),true);
    }
}
inline void restore(Bytes&input,uint32_t width,uint32_t height,Layout layout){
    size_t pixels=rowfilter::pixels(width,height);need(input.size()==pixels*3&&unsigned(layout)<=3);
    bool planar=unsigned(layout)&1;size_t domains=planar?3:1,length=planar?pixels:pixels*3,stride=size_t(width)*(planar?1:3);
    for(size_t p=0;p<domains;++p)for(size_t row=stride;row<length;row+=stride)
        rowfilter::detail::add_row(std::span(input).subspan(p*length+row,stride),View(input).subspan(p*length+row-stride,stride),true);
}
inline View encode(std::span<const uint32_t>rgba,uint32_t width,uint32_t height,Layout layout,bool up,Workspace&w){
    size_t count=rowfilter::pixels(width,height);need(rgba.size()==count&&unsigned(layout)<=3&&!alias(bytes(rgba),w));
    w.rgb.resize(count*3);rowfilter::detail::pack(rgba,w.rgb,true);View candidate=w.rgb;
    if(layout!=Layout::rgb){w.ordered.resize(w.rgb.size());order(w.rgb,w.ordered,layout);candidate=w.ordered;}
    if(up){predict(candidate,width,height,layout,w.filtered);candidate=w.filtered;}
    w.compressed.resize(size_t(LZ4_compressBound(int(candidate.size()))));
    int n=LZ4_compress_default(reinterpret_cast<const char*>(candidate.data()),reinterpret_cast<char*>(w.compressed.data()),int(candidate.size()),int(w.compressed.size()));need(n>0);
    bool compressed=size_t(n)+64<=w.rgb.size();View body=compressed?View(w.compressed).first(size_t(n)):View(w.rgb);
    w.wire.assign(header_bytes,0);w.wire[0]='R';w.wire[1]='C';w.wire[2]='P';w.wire[3]='1';w.wire[4]=1;
    w.wire[5]=compressed?uint8_t(up?2:1):0;w.wire[6]=compressed?uint8_t(layout):0;
    put32(w.wire,8,width);put32(w.wire,12,height);put32(w.wire,16,uint32_t(body.size()));put32(w.wire,20,uint32_t(w.rgb.size()));
    put32(w.wire,24,wpc::crc(body));put32(w.wire,28,wpc::crc(View(w.wire).first(28)));w.wire.insert(w.wire.end(),body.begin(),body.end());return w.wire;
}
struct Shape {uint32_t width,height,mode;Layout layout;size_t raw_bytes;};
inline Shape inspect(View wire){
    need(wire.size()>header_bytes&&wire.size()<=max_wire);
    need(wire[0]=='R'&&wire[1]=='C'&&wire[2]=='P'&&wire[3]=='1'&&wire[4]==1&&wire[5]<=2&&wire[6]<=3&&wire[7]==0);
    Shape s{wpc::u32(wire,8),wpc::u32(wire,12),wire[5],Layout(wire[6]),0};s.raw_bytes=rowfilter::pixels(s.width,s.height)*3;
    size_t stored=wpc::u32(wire,16);need(stored==wire.size()-header_bytes&&wpc::u32(wire,20)==s.raw_bytes);
    need(s.mode?stored<s.raw_bytes:(stored==s.raw_bytes&&s.layout==Layout::rgb));
    need(wpc::u32(wire,28)==wpc::crc(wire.first(28))&&wpc::u32(wire,24)==wpc::crc(wire.subspan(header_bytes)));
    if(s.mode)rowfilter::validate_lz4(wire.subspan(header_bytes),s.raw_bytes);return s;
}
inline Shape decode_into(View wire,std::span<uint32_t>output,Workspace&w){
    need(output.size()<=rowfilter::max_pixels&&!alias(wire,w)&&!alias(bytes(output),w)&&!wpc::overlaps(wire,bytes(output)));
    try{auto s=inspect(wire);need(output.size()==s.raw_bytes/3);w.decoded.resize(s.raw_bytes);auto body=wire.subspan(header_bytes);
        if(s.mode){int n=LZ4_decompress_safe(reinterpret_cast<const char*>(body.data()),reinterpret_cast<char*>(w.decoded.data()),int(body.size()),int(w.decoded.size()));need(n==int(w.decoded.size()));}
        else std::copy(body.begin(),body.end(),w.decoded.begin());
        if(s.mode==2)restore(w.decoded,s.width,s.height,s.layout);
        View rgb=w.decoded;if(s.layout!=Layout::rgb){w.rgb.resize(s.raw_bytes);unorder(w.decoded,w.rgb,s.layout);rgb=w.rgb;}
        rowfilter::detail::expand(rgb,output,true);return s;
    }catch(...){w.rgb.clear();w.decoded.clear();throw;}
}
}
