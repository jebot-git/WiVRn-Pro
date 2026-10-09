// SPDX-License-Identifier: MIT
#include "channelplanes.hpp"
#include <fstream>
#include <iostream>
#include <random>
using namespace channelplanes;
static size_t trips=0,rejections=0;
template<class F>static void reject(F f){bool failed=false;try{f();}catch(const std::runtime_error&){failed=true;}need(failed);++rejections;}
static Bytes read(const char*p){std::ifstream f(p,std::ios::binary);need(bool(f));return {std::istreambuf_iterator<char>(f),{}};}
static void repair(Bytes&b){put32(b,24,wpc::crc(View(b).subspan(header_bytes)));put32(b,28,wpc::crc(View(b).first(28)));}
int main(int argc,char**argv){try{
    if(argc==3){auto wire=read(argv[1]),rgba=read(argv[2]);auto shape=inspect(wire);need(rgba.size()==shape.raw_bytes/3*4);Workspace w;std::vector<uint32_t>out(rgba.size()/4);
        decode_into(wire,out,w);need(std::equal(rgba.begin(),rgba.end(),bytes(out).begin()));std::cout<<"{\"golden_exact\":true}\n";return 0;}
    need(argc==1);std::mt19937 rng(20261009);Workspace tx,rx;rowfilter::Workspace reference;
    for(auto [width,height]:{std::pair{1u,1u},{1u,17u},{17u,1u},{17u,17u},{35u,65u},{576u,640u}})
        for(unsigned kind=0;kind<5;++kind){std::vector<uint32_t>input(size_t(width)*height),output(input.size());
            for(unsigned y=0;y<height;++y)for(unsigned x=0;x<width;++x){unsigned c=kind==0?0x123456:kind==1?((x+y*7)&255)*0x010101:kind==2?((x*19+y*3)&255)|(((y*7)&255)<<8)|(((x*2+y*11)&255)<<16):kind==3?((x/16+y/16)%2?0xff1234:0x99cc):rng();input[size_t(y)*width+x]=c|0xff000000;}
            for(unsigned layout=0;layout<4;++layout)for(bool up:{false,true}){auto wire=encode(input,width,height,Layout(layout),up,tx);decode_into(wire,output,rx);need(output==input);++trips;
                if(layout==0){auto old=rowfilter::encode(input,width,height,up?rowfilter::Policy::up:rowfilter::Policy::plain,reference);need(wire[5]==old[5]&&wire.size()==old.size()&&std::equal(wire.begin()+header_bytes,wire.end(),old.begin()+header_bytes));}
            }
        }
    Bytes rgb{250,1,127,4,255,128,2,0,1,7,1,254},ordered(12),restored(12),filtered;
    order(rgb,ordered,Layout::green_delta_planes);need(ordered==Bytes({1,255,0,1,249,5,2,6,126,129,1,253}));
    predict(ordered,2,2,Layout::green_delta_planes,filtered);need(filtered==Bytes({1,255,255,2,249,5,9,1,126,129,131,124}));
    restore(filtered,2,2,Layout::green_delta_planes);need(filtered==ordered);unorder(filtered,restored,Layout::green_delta_planes);need(restored==rgb);
    reject([&]{order(rgb,rgb,Layout::planes);});reject([&]{unorder(rgb,rgb,Layout::planes);});reject([&]{predict(rgb,2,2,Layout::planes,rgb);});
    reject([&]{restore(rgb,2,2,Layout(4));});
    std::vector<uint32_t>input(17*17,0xff123456),output(input.size(),0x76543210);auto v=encode(input,17,17,Layout::green_delta_planes,true,tx);Bytes original(v.begin(),v.end());need(original[5]==2&&original[6]==3);
    auto unchanged=[&]{need(rx.rgb.empty()&&rx.decoded.empty());need(std::all_of(output.begin(),output.end(),[](auto p){return p==0x76543210;}));};
    for(size_t n=0;n<original.size();++n){reject([&]{decode_into(View(original).first(n),output,rx);});unchanged();}
    for(size_t at:{size_t(0),size_t(4),size_t(5),size_t(6),size_t(7),size_t(8),size_t(12),size_t(16),size_t(20)}){auto bad=original;bad[at]^=0x80;repair(bad);reject([&]{decode_into(bad,output,rx);});unchanged();}
    auto bad=original;bad.push_back(0);repair(bad);reject([&]{decode_into(bad,output,rx);});unchanged();
    for(unsigned i=0;i<10000;++i){bad=original;bad[rng()%bad.size()]^=uint8_t(1u<<(rng()%8));if(i%2==0)repair(bad);output.assign(input.size(),0x76543210);
        bool failed=false;try{decode_into(bad,output,rx);}catch(const std::runtime_error&){failed=true;++rejections;}
        if(failed)unchanged();else need(std::all_of(output.begin(),output.end(),[](auto p){return p>>24==255;}));}
    std::vector<uint32_t>maximum(rowfilter::max_pixels),maxout(maximum.size());for(auto&p:maximum)p=rng()|0xff000000;
    v=encode(maximum,4096,1024,Layout::green_delta_planes,true,tx);need(v[5]==0&&v[6]==0);decode_into(v,maxout,rx);need(maxout==maximum);++trips;
    std::fill(maximum.begin(),maximum.end(),0xff123456);v=encode(maximum,1024,4096,Layout::green_delta_planes,true,tx);decode_into(v,maxout,rx);need(maxout==maximum);++trips;
    std::vector<uint32_t>one{0xffaabbcc},outone(1);v=encode(one,1,1,Layout::planes,true,tx);bad=Bytes(v.begin(),v.end());bad[6]=1;repair(bad);reject([&]{decode_into(bad,outone,rx);});
    reject([&]{encode(input,17,17,Layout(4),true,tx);});input.back()&=0x7fffffff;reject([&]{encode(input,17,17,Layout::planes,true,tx);});
    reject([&]{encode(maximum,4096,1025,Layout::planes,true,tx);});reject([&]{decode_into(tx.wire,outone,tx);});
    std::cout<<"{\"pass\":true,\"roundtrips\":"<<trips<<",\"rejections\":"<<rejections<<",\"mutations\":10000,\"maximum_pixels\":"<<rowfilter::max_pixels<<",\"independent_modulo_planes_oracle\":true,\"layout0_body_matches_rrf\":true,\"output_unchanged_on_rejection\":true}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
