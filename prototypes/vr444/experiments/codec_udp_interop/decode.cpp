// SPDX-License-Identifier: MIT
#include "../rgb_rowblocks/rowblocks.hpp"
#include "codec.hpp"
#include <iostream>
using Bytes=rowfilter::Bytes;
static void need(bool b,const char*m){if(!b)throw std::runtime_error(m);}
static void read_bytes(void*dst,size_t n){std::cin.read(static_cast<char*>(dst),std::streamsize(n));need(bool(std::cin),"truncated decoder input");}
static uint32_t get32(){std::array<uint8_t,4>b{};read_bytes(b.data(),4);return uint32_t(b[0])|uint32_t(b[1])<<8|uint32_t(b[2])<<16|uint32_t(b[3])<<24;}
static void put32(uint32_t n){std::array<uint8_t,4>b{};for(unsigned i=0;i<4;++i)b[i]=uint8_t(n>>(8*i));std::cout.write(reinterpret_cast<char*>(b.data()),b.size());}
static Bytes bytes(size_t max){size_t n=get32();need(n>0&&n<=max,"decoder input bound");Bytes out(n);read_bytes(out.data(),out.size());return out;}
struct Decoded{uint32_t width,height;std::vector<uint32_t>rgba;};
static Decoded decode(const Bytes&wire,unsigned mode){
    if(mode==0){Bytes raw=wire;if(wire.size()>=4&&std::equal(wire.begin(),wire.begin()+4,"WPC1")){wpc::Workspace w;wpc::decode(wire,w);raw=std::move(w.decoded);}auto f=vr444::unpack(raw);return {f.width,f.height,vr444::decode(f)};}
    if(mode==1){auto shape=rowfilter::inspect(wire);Decoded out{shape.width,shape.height,std::vector<uint32_t>(size_t(shape.width)*shape.height)};rowfilter::Workspace w;rowfilter::decode_into(wire,out.rgba,w);return out;}
    auto index=rowblocks::inspect(wire);Decoded out{index.width,index.height,std::vector<uint32_t>(size_t(index.width)*index.height)};rowblocks::Workspace w;rowblocks::decode_into(wire,out.rgba,w);return out;
}
static void exact(const Decoded&d,const Bytes&expected){need(expected.size()==d.rgba.size()*4,"RGBA expected shape");for(size_t i=0;i<d.rgba.size();++i)for(unsigned c=0;c<4;++c)need(expected[i*4+c]==uint8_t(d.rgba[i]>>(8*c)),"RGBA bytes differ");}
static Bytes baseline_wire(const Bytes&raw){if(raw.size()>wpc::input_limit)return raw;wpc::Workspace w;wpc::encode(raw,wpc::wire_limit,w);if(w.wire[5]==1&&w.wire.size()+64<=raw.size())return w.wire;return raw;}
int main(int argc,char**argv){try{
    if(argc==2&&std::string(argv[1])=="encode"){
        std::array<char,4>magic{};read_bytes(magic.data(),4);need(magic==std::array<char,4>{'C','E','P','1'},"encoder magic");
        auto left=bytes(16*1024*1024),right=bytes(16*1024*1024);need(std::cin.peek()==std::char_traits<char>::eof(),"encoder trailing input");
        auto l=baseline_wire(left),r=baseline_wire(right);std::cout.write("CEY1",4);for(const auto*p:{&l,&r}){put32(uint32_t(p->size()));std::cout.write(reinterpret_cast<const char*>(p->data()),std::streamsize(p->size()));}need(bool(std::cout),"encoder output failure");return 0;
    }
    need(argc==1,"decoder takes no arguments");
    std::array<char,4>magic{};read_bytes(magic.data(),4);need(magic==std::array<char,4>{'C','D','P','1'},"decoder magic");unsigned mode=get32();need(mode<=2,"decoder mode");
    auto left=bytes(262144),right=bytes(262144);need(left.size()+right.size()<=262144,"complete encoded pair bound");
    auto expected_left=bytes(rowfilter::max_pixels*4),expected_right=bytes(rowfilter::max_pixels*4);
    need(std::cin.peek()==std::char_traits<char>::eof(),"decoder trailing input");
    // Both complete eyes stay private; no success marker or pixels escape if
    // right-eye syntax, dimensions, reconstruction or expected bytes fail.
    auto l=decode(left,mode),r=decode(right,mode);need(l.width==r.width&&l.height==r.height,"stereo dimensions differ");exact(l,expected_left);exact(r,expected_right);
    std::cout<<"{\"status\":\"pass\",\"both_eyes_exact\":true,\"width\":"<<l.width<<",\"height\":"<<l.height<<",\"rgba_bytes_per_eye\":"<<expected_left.size()<<"}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
