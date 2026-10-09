// SPDX-License-Identifier: MIT
#include "rowblocks.hpp"
#include <fstream>
#include <iostream>
#include <random>
using namespace rowblocks;
static size_t trips=0,rejections=0;
template<class F>static void reject(F f){bool failed=false;try{f();}catch(const std::runtime_error&){failed=true;}need(failed);++rejections;}
static void repair(Bytes&b){size_t n=wpc::u32(b,16);need(b.size()>=header_bytes+n*entry_bytes);put32(b,24,wpc::crc(View(b).subspan(header_bytes,n*entry_bytes)));put32(b,28,wpc::crc(View(b).first(28)));}
static Bytes read(const char*p){std::ifstream f(p,std::ios::binary);need(bool(f));return {std::istreambuf_iterator<char>(f),{}};}
int main(int argc,char**argv){try{
    if(argc==3){auto wire=read(argv[1]),expected=read(argv[2]);auto index=inspect(wire);need(expected.size()==size_t(index.width)*index.height*4);
        std::vector<uint32_t>output(expected.size()/4);Workspace rx;decode_into(wire,output,rx);
        need(std::equal(bytes(output).begin(),bytes(output).end(),expected.begin(),expected.end()));std::cout<<"{\"golden_exact\":true}\n";return 0;}
    need(argc==1);std::mt19937 rng(20261009);Workspace tx,rx,scalar;
    for(auto [width,height]:{std::pair{1u,1u},{1u,16u},{1u,17u},{17u,31u},{17u,32u},{17u,33u},{35u,63u},{35u,64u},{35u,65u},{17u,127u},{17u,128u},{17u,129u},{1u,4096u},{576u,640u}})
        for(unsigned kind=0;kind<4;++kind){std::vector<uint32_t>input(size_t(width)*height),output(input.size());
            for(unsigned y=0;y<height;++y)for(unsigned x=0;x<width;++x){unsigned color=kind==0?0x123456:kind==1?(x*19+y*7)*0x010101:kind==2?((x/16+y/16)%2?0xff1234:0x99cc):rng();input[size_t(y)*width+x]=color|0xff000000u;}
            for(uint32_t rows:{16,32,64,128})for(Policy policy:{Policy::plain,Policy::up,Policy::adaptive}){
                auto wire=encode(input,width,height,rows,policy,tx,true);auto reference=encode(input,width,height,rows,policy,scalar,false);
                need(std::equal(wire.begin(),wire.end(),reference.begin(),reference.end()));auto index=unpack_rgb(wire,rx);
                need(index.blocks.size()==(height+rows-1)/rows);
                for(auto&b:index.blocks){Bytes rgb(size_t(width)*b.height*3),residual;
                    rowfilter::detail::pack(std::span(input).subspan(size_t(b.row)*width,size_t(b.height)*width),rgb,false);
                    if(b.mode==2){rowfilter::predict(rgb,size_t(width)*3,residual,false);rgb=std::move(residual);}
                    need(std::equal(rgb.begin(),rgb.end(),rx.rgb.begin()+b.raw_offset));}
                for(bool simd:{false,true}){decode_into(wire,output,rx,simd);need(output==input);++trips;}
            }
        }
    std::vector<uint32_t>input(17*65,0xffaabbcc),output(input.size(),0x87654321);auto v=encode(input,17,65,16,Policy::up,tx);Bytes original(v.begin(),v.end());
    auto unchanged=[&]{need(rx.rgb.empty());need(std::all_of(output.begin(),output.end(),[](auto p){return p==0x87654321;}));};
    for(size_t n=0;n<original.size();++n){output.assign(input.size(),0x87654321);reject([&]{decode_into(View(original).first(n),output,rx);});unchanged();}
    for(size_t at:{size_t(0),size_t(4),size_t(5),size_t(6),size_t(7),size_t(8),size_t(12),size_t(20),size_t(32),size_t(36),size_t(40),size_t(44)}){
        auto bad=original;bad[at]^=0x80;repair(bad);reject([&]{decode_into(bad,output,rx);});unchanged();}
    // Count cannot be used to allocate or index before dimensions imply it.
    auto bad=original;put32(bad,16,UINT32_MAX);put32(bad,28,wpc::crc(View(bad).first(28)));reject([&]{decode_into(bad,output,rx);});unchanged();
    auto index=inspect(original);
    // A late corrupt block must leave the entire caller output untouched.
    for(auto&b:index.blocks){bad=original;bad[b.offset+b.length-1]^=1;reject([&]{decode_into(bad,output,rx);});unchanged();}
    // CRC-valid block-local malformed grammar still rejects before any output.
    bad=original;auto b=index.blocks.front();bad[b.offset+rowfilter::header_bytes]=0xff;
    put32(bad,b.offset+24,wpc::crc(View(bad).subspan(b.offset+32,b.length-32)));
    put32(bad,b.offset+28,wpc::crc(View(bad).subspan(b.offset,28)));
    reject([&]{decode_into(bad,output,rx);});unchanged();
    // Mutations repaired through both checksum layers exercise table and block
    // semantics; valid altered raw pixels are allowed because CRC is not auth.
    for(unsigned i=0;i<10000;++i){bad=original;size_t at=rng()%bad.size();bad[at]^=uint8_t(1u<<(rng()%8));
        if(i%2==0){for(auto&e:index.blocks){put32(bad,e.offset+24,wpc::crc(View(bad).subspan(e.offset+32,e.length-32)));put32(bad,e.offset+28,wpc::crc(View(bad).subspan(e.offset,28)));}
            if(wpc::u32(bad,16)==index.blocks.size())repair(bad);}
        output.assign(input.size(),0x87654321);bool failed=false;try{decode_into(bad,output,rx);}catch(const std::runtime_error&){failed=true;++rejections;}
        if(failed)unchanged();else need(std::all_of(output.begin(),output.end(),[](auto p){return p>>24==255;}));
    }
    // Maximum high-entropy raw fallback still preserves alpha and all bytes.
    std::vector<uint32_t>maximum(rowfilter::max_pixels),maxout(maximum.size());for(auto&p:maximum)p=rng()|0xff000000;
    auto wire=encode(maximum,4096,1024,16,Policy::adaptive,tx);auto maxindex=decode_into(wire,maxout,rx);need(maxout==maximum);++trips;
    need(std::all_of(maxindex.blocks.begin(),maxindex.blocks.end(),[](auto&e){return e.mode==0;}));
    for(uint32_t rows:{0,1,15,17,256})reject([&]{encode(input,17,65,rows,Policy::up,tx);});
    reject([&]{encode(input,17,65,16,Policy(0),tx);});input.back()&=0x7fffffff;reject([&]{encode(input,17,65,16,Policy::up,tx);});
    output.assign(input.size(),0x87654321);reject([&]{decode_into(original,std::span(output).first(output.size()-1),rx);});unchanged();
    reject([&]{decode_into(tx.wire,maxout,tx);});reject([&]{encode(maximum,4096,1025,16,Policy::plain,tx);});
    std::cout<<"{\"pass\":true,\"roundtrips\":"<<trips<<",\"rejections\":"<<rejections<<",\"mutations\":10000,\"maximum_pixels\":"<<rowfilter::max_pixels<<",\"maximum_blocks\":256,\"output_unchanged_on_rejection\":true,\"scalar_simd_wire_equal\":true,\"backend\":\""<<rowfilter::detail::backend()<<"\"}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
