// SPDX-License-Identifier: MIT
#include "rowfilter.hpp"
#include <fstream>
#include <iostream>
#include <random>
using namespace rowfilter;
static size_t trips=0,rejections=0;
template<class F>static void reject(F f){bool failed=false;try{f();}catch(const std::runtime_error&){failed=true;}need(failed);++rejections;}
static void repair(Bytes&b){put32(b,24,wpc::crc(View(b).subspan(header_bytes)));put32(b,28,wpc::crc(View(b).first(28)));}
static Bytes read(const char*path){std::ifstream f(path,std::ios::binary);need(bool(f));return {std::istreambuf_iterator<char>(f),{}};}
int main(int argc,char**argv){try{
    if(argc==3){auto wire=read(argv[1]),expected=read(argv[2]);auto shape=inspect(wire);need(expected.size()==shape.raw_bytes/3*4);std::vector<uint32_t>out(shape.raw_bytes/3);Workspace rx;decode_into(wire,out,rx);need(std::equal(bytes(out).begin(),bytes(out).end(),expected.begin(),expected.end()));std::cout<<"{\"golden_exact\":true}\n";return 0;}
    need(argc==1);std::mt19937 rng(20261009);Workspace tx,rx;
    for(auto [width,height]:{std::pair{1u,1u},{1u,17u},{17u,1u},{15u,16u},{16u,15u},{16u,16u},{17u,17u},{35u,65u},{576u,640u}})
        for(unsigned kind=0;kind<5;++kind){std::vector<uint32_t>input(size_t(width)*height),output(input.size());
            for(unsigned y=0;y<height;++y)for(unsigned x=0;x<width;++x){unsigned color=kind==0?0:kind==1?0x123456:kind==2?(x*19+y*7)*0x010101:kind==3?((x/16+y/16)%2?0xff1234:0x99cc):rng();input[size_t(y)*width+x]=color|0xff000000u;}
            for(Policy p:{Policy::plain,Policy::up,Policy::adaptive}){auto wire=encode(input,width,height,p,tx);auto shape=unpack_rgb(wire,rx);
                Bytes expected(input.size()*3);for(size_t i=0;i<input.size();++i)for(unsigned c=0;c<3;++c)expected[3*i+c]=uint8_t(input[i]>>(8*c));
                if(shape.mode==2){Bytes residual;predict(expected,size_t(width)*3,residual);need(rx.rgb==residual);}else need(rx.rgb==expected);
                decode_into(wire,output,rx);need(output==input);++trips;}
        }
    // Independent row carry check, including modulo wrap and first-row identity.
    Bytes a{250,1,127,4,255,128,2,0,1,7,1,254},filtered;predict(a,6,filtered);
    need(filtered==Bytes({250,1,127,4,255,128,8,255,130,3,2,126}));restore(filtered,6);need(filtered==a);
    // LZ4 safe-decode alone accepts offset0; strict documented grammar must not.
    Bytes block{0x3f,12,34,56,3,0,21,0x50,34,56,12,34,56};validate_lz4(block,48);
    auto zero=block;zero[4]=0;reject([&]{validate_lz4(zero,48);});
    auto far=block;far[4]=4;reject([&]{validate_lz4(far,48);});
    auto no_final=block;no_final.resize(7);reject([&]{validate_lz4(no_final,43);});
    auto short_final=block;short_final[7]=0x40;short_final.pop_back();reject([&]{validate_lz4(short_final,47);});
    reject([&]{validate_lz4(Bytes{0xf0,255},48);});reject([&]{validate_lz4(Bytes{0x10,0,1},48);});
    std::vector<uint32_t>input(17*17,0xffaabbcc),output(input.size(),0x87654321u);auto span=encode(input,17,17,Policy::up,tx);Bytes original(span.begin(),span.end());need(original[5]==2);
    for(size_t n=0;n<original.size();++n){output.assign(input.size(),0x87654321u);reject([&]{decode_into(View(original).first(n),output,rx);});need(std::all_of(output.begin(),output.end(),[](auto p){return p==0x87654321u;}));}
    for(size_t at:{size_t(0),size_t(4),size_t(5),size_t(6),size_t(7),size_t(8),size_t(12),size_t(16),size_t(20)}){
        auto bad=original;bad[at]^=0x80;repair(bad);reject([&]{decode_into(bad,output,rx);});need(rx.rgb.empty());}
    auto bad=original;bad.push_back(0);repair(bad);reject([&]{decode_into(bad,output,rx);});
    // CRC repair deliberately allows arbitrary legal RGB content; this CRC is
    // not authentication. Mutated frames may succeed if grammar and bounds hold.
    for(unsigned i=0;i<10000;++i){bad=original;bad[rng()%bad.size()]^=uint8_t(1u<<(rng()%8));if(i%2==0)repair(bad);
        output.assign(input.size(),0x87654321u);bool failed=false;try{decode_into(bad,output,rx);}catch(const std::runtime_error&){failed=true;++rejections;}
        if(failed){need(rx.rgb.empty());need(std::all_of(output.begin(),output.end(),[](auto p){return p==0x87654321u;}));}
        else need(std::all_of(output.begin(),output.end(),[](auto p){return (p>>24)==255;}));
    }
    std::vector<uint32_t>maximum(max_pixels,0xff123456),maxout(max_pixels);span=encode(maximum,4096,1024,Policy::adaptive,tx);decode_into(span,maxout,rx);need(maxout==maximum);++trips;
    reject([&]{encode(maximum,4096,1025,Policy::plain,tx);});input[0]=0x7faabbcc;reject([&]{encode(input,17,17,Policy::plain,tx);});
    reject([&]{decode_into(original,std::span<uint32_t>(output).first(output.size()-1),rx);});
    reject([&]{decode_into(tx.wire,maxout,tx);});
    std::cout<<"{\"pass\":true,\"roundtrips\":"<<trips<<",\"rejections\":"<<rejections<<",\"mutations\":10000,\"maximum_pixels\":"<<max_pixels<<",\"output_unchanged_on_rejection\":true,\"independent_modulo_predictor_oracle\":true}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
