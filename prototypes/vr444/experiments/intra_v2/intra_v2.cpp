// SPDX-License-Identifier: MIT
#include "intra_v2.hpp"
#include <algorithm>
#include <array>
#include <bit>
#include <limits>
#include <random>
#include <stdexcept>
namespace intra_v2 {
namespace {
void check(bool good,const char* why) { if(!good)throw std::runtime_error(why); }
unsigned blocks(unsigned w,unsigned h) {
    check(w&&h&&w<=4096&&h<=4096&&uint64_t(w)*h<=8388608,"dimensions");
    return ((w+15)/16)*((h+15)/16);
}
unsigned zig(int x){return x<0?unsigned(-x*2-1):unsigned(x*2);}
int unzig(unsigned x){return x&1?-int((x+1)/2):int(x/2);}
uint32_t mask(unsigned n){return n==32?UINT32_MAX:((uint32_t(1)<<n)-1);}
struct Writer {
    std::vector<uint8_t> data;
    unsigned bit=0;
    void put(uint32_t value,unsigned n) {
        check(n<=32&&bit+n<=vr444::SLOT_WORDS*32,"writer bound");
        check(n==32 || (value>>n)==0,"value width");
        while(n){unsigned take=std::min(n,8-bit%8);if(bit/8==data.size())data.push_back(0);
            data[bit/8]|=uint8_t((value&mask(take))<<(bit%8));value>>=take;bit+=take;n-=take;}
    }
};
struct Reader {
    std::span<const uint8_t> data;unsigned bit=0;
    uint32_t get(unsigned n){
        check(n<=32&&uint64_t(bit)+n<=uint64_t(data.size())*8,"truncated bits");
        uint32_t value=0;unsigned shift=0;
        while(n){unsigned take=std::min(n,8-bit%8);value|=((data[bit/8]>>(bit%8))&mask(take))<<shift;
            shift+=take;bit+=take;n-=take;}
        return value;
    }
    void end(unsigned alignment=8){
        check((bit+alignment-1)/alignment*alignment==data.size()*8,"trailing tile data");
        while(bit<data.size()*8)check(get(1)==0,"nonzero padding");
    }
};
using Coeff=std::array<int,768>;
void coefficient_check(unsigned c,unsigned i,int value,unsigned q){
    if(i==0)check((c==0 && value>=0 && value<=255)||(c>0 && value>=-255 && value<=255),"DC range");
    else check(std::abs(value*int(q))<=1536,"AC range");
}
Coeff read_coeff(std::span<const uint8_t> bytes,unsigned q,unsigned grain,bool legacy){
    Reader bits{bytes};Coeff values{};
    for(unsigned c=0;c<3;++c){
        values[c*256]=unzig(bits.get(11));coefficient_check(c,0,values[c*256],q);
        for(unsigned base=1;base<256;base+=grain){
            unsigned length=std::min(grain,256-base);
            if(!bits.get(1))continue;
            unsigned width=bits.get(4),mode=bits.get(2);
            check(width>=1&&width<=11&&mode<=2,"coefficient coding mode");
            unsigned common=mode==0?bits.get(width):0;
            unsigned sparse=mode==2?bits.get(legacy?16:length):mask(length);
            check(!legacy || length==16 || mode!=2 || !(sparse&0x8000),"sparse tail");
            for(unsigned j=0;j<length;++j){
                auto value=mode==0?common:((sparse&(uint32_t(1)<<j))?bits.get(width):0);
                values[c*256+base+j]=unzig(value);coefficient_check(c,base+j,values[c*256+base+j],q);
            }
        }
    }
    bits.end(legacy?32:8);return values;
}
std::vector<uint8_t> write_coeff(const Coeff& a,unsigned grain,bool legacy){
    Writer bits;
    for(unsigned c=0;c<3;++c){
        bits.put(zig(a[c*256]),11);
        for(unsigned base=1;base<256;base+=grain){
            unsigned length=std::min(grain,256-base),mx=0,sparse=0,count=0;bool same=true;
            for(unsigned j=0;j<length;++j){auto v=a[c*256+base+j];mx=std::max(mx,zig(v));same &=v==a[c*256+base];if(v){sparse|=uint32_t(1)<<j;++count;}}
            unsigned width=std::bit_width(mx);bits.put(width!=0,1);
            if(!width)continue;
            unsigned sparse_width=legacy?16:length;
            unsigned mode=same?0:(sparse_width+count*width<length*width?2:1);
            bits.put(width,4);bits.put(mode,2);
            if(mode==0)bits.put(zig(a[c*256+base]),width);
            else{
                if(mode==2)bits.put(sparse,sparse_width);
                for(unsigned j=0;j<length;++j)if(mode==1 || a[c*256+base+j])bits.put(zig(a[c*256+base+j]),width);
            }
        }
    }
    if(legacy)while(bits.bit%32)bits.put(0,1);
    return bits.data;
}
void put(std::vector<uint8_t>& out,uint32_t v,unsigned n){for(unsigned i=0;i<n;++i)out.push_back(uint8_t(v>>(i*8)));}
struct Bytes {
    std::span<const uint8_t> data;size_t at=0;
    uint32_t get(unsigned n){check(n<=4 && n<=data.size()-at,"truncated record");uint32_t v=0;for(unsigned i=0;i<n;++i)v|=uint32_t(data[at++])<<(i*8);return v;}
    std::span<const uint8_t> take(size_t n){check(n<=data.size()-at,"record bound");auto out=data.subspan(at,n);at+=n;return out;}
};
std::vector<uint8_t> tile_bytes(const vr444::Frame& f,unsigned tile){
    std::vector<uint8_t> out;out.reserve(f.lengths[tile]*4);
    for(unsigned i=0;i<f.lengths[tile];++i)put(out,f.slots[size_t(tile)*vr444::SLOT_WORDS+i],4);
    return out;
}
void store_tile(vr444::Frame& f,unsigned tile,std::span<const uint8_t> bytes){
    check(bytes.size()%4==0&&bytes.size()<=vr444::SLOT_WORDS*4,"expanded tile");
    f.lengths[tile]=bytes.size()/4;Bytes input{bytes};
    for(unsigned i=0;i<f.lengths[tile];++i)f.slots[size_t(tile)*vr444::SLOT_WORDS+i]=input.get(4);
}
}
std::vector<uint8_t> encode(const vr444::Frame& f,Stats* stats,bool adaptive){
    unsigned count=blocks(f.width,f.height);
    check(f.q>=1&&f.q<=1024&&f.lengths.size()==count&&f.slots.size()==size_t(count)*vr444::SLOT_WORDS,"frame shape");
    check(f.tile_q.empty()||f.tile_q.size()==count,"quantizer map");
    std::vector<uint8_t> out;out.reserve(vr444::payload_bytes(f)+4);
    // VRI2 magic, version=1, dimensions, q, count, flags (bit0: tile q map).
    for(auto v:{0x32495256u,1u,f.width,f.height,f.q,count,unsigned(!f.tile_q.empty())})put(out,v,4);
    for(unsigned tile=0;tile<count;++tile){
        unsigned q=f.tile_q.empty()?f.q:f.tile_q[tile];check(q>=1&&q<=1024,"tile q");
        check(f.lengths[tile]>=3&&f.lengths[tile]<=vr444::SLOT_WORDS,"legacy length");
        auto original=tile_bytes(f,tile);
        unsigned mode=0;auto best=original;
        auto store_dc=[&](int y,int co,int cg){Writer bits;bits.put(y,8);bits.put(co+255,9);bits.put(cg+255,9);best=std::move(bits.data);mode=1;};
        if(!adaptive){
            // Like legacy pack(), the caller supplies a validated/produced Frame.
            // All-zero AC uses exactly 11 DC + 16 zero flags per channel.
            // Three words alone are not sufficient: nonzero groups can also fit.
            if(f.lengths[tile]==3){
                Reader bits{original};std::array<int,3> dc{};bool constant=true;
                for(unsigned c=0;c<3;++c){dc[c]=unzig(bits.get(11));coefficient_check(c,0,dc[c],q);if(bits.get(16)!=0){constant=false;break;}}
                if(constant){bits.end(32);store_dc(dc[0],dc[1],dc[2]);}
            }
        }else{
            auto a=read_coeff(original,q,16,true);bool constant=true;
            for(unsigned c=0;c<3;++c)for(unsigned i=1;i<256;++i)constant &= a[c*256+i]==0;
            if(constant)store_dc(a[0],a[256],a[512]);
            else for(unsigned grain:{8u,16u,32u}){
                auto candidate=write_coeff(a,grain,false);
                if(candidate.size()<best.size()){best=std::move(candidate);mode=grain==8?2:(grain==16?3:4);}
            }
        }
        put(out,unsigned(best.size())*8+mode,2);if(!f.tile_q.empty())put(out,q,2);
        out.insert(out.end(),best.begin(),best.end());if(stats)++stats->modes[mode];
    }
    if(stats){stats->legacy_bytes+=vr444::payload_bytes(f);stats->wire_bytes+=out.size();}
    return out;
}
vr444::Frame decode(std::span<const uint8_t> wire){
    Bytes input{wire};check(input.get(4)==0x32495256u,"magic");check(input.get(4)==1,"version");
    unsigned w=input.get(4),h=input.get(4),q=input.get(4),count=input.get(4),flags=input.get(4);
    check(count==blocks(w,h)&&q>=1&&q<=1024&&flags<=1,"frame header");
    size_t records=input.at;
    // Validate every record boundary and coding tag before allocating expanded slots.
    for(unsigned tile=0;tile<count;++tile){
        unsigned record=input.get(2),mode=record&7,length=record>>3;
        if(flags){unsigned tq=input.get(2);check(tq>=1&&tq<=1024,"tile q");}
        check(mode<=4&&length>=4&&length<=vr444::SLOT_WORDS*4,"tile tag/length");
        check(mode!=0 || (length>=12&&length%4==0),"legacy record length");
        check(mode!=1 || length==4,"constant record length");input.take(length);
    }
    check(input.at==wire.size(),"trailing frame data");input.at=records;
    vr444::Frame f{w,h,q,std::vector<uint32_t>(size_t(count)*vr444::SLOT_WORDS),std::vector<uint32_t>(count),{}};
    if(flags)f.tile_q.resize(count);
    for(unsigned tile=0;tile<count;++tile){
        unsigned record=input.get(2),mode=record&7,length=record>>3;
        unsigned tq=flags?(f.tile_q[tile]=input.get(2)):q;auto bytes=input.take(length);
        if(mode==0){read_coeff(bytes,tq,16,true);store_tile(f,tile,bytes);continue;}
        Coeff a{};
        if(mode==1){
            Reader bits{bytes};a[0]=bits.get(8);a[256]=int(bits.get(9))-255;a[512]=int(bits.get(9))-255;
            coefficient_check(0,0,a[0],tq);coefficient_check(1,0,a[256],tq);coefficient_check(2,0,a[512],tq);bits.end();
        }else a=read_coeff(bytes,tq,mode==2?8:(mode==3?16:32),false);
        auto original=write_coeff(a,16,true);store_tile(f,tile,original);
    }
    return f;
}
void self_test(){
    for(auto dims:{std::array<unsigned,2>{1,1},{17,19},{64,48}})for(unsigned kind=0;kind<3;++kind)for(unsigned q:{1u,8u,17u,1024u}){
        auto source=vr444::scene(dims[0],dims[1],kind);auto frame=vr444::encode(source,dims[0],dims[1],q);
        auto wire=encode(frame);auto restored=decode(wire);check(vr444::decode(frame)==vr444::decode(restored),"RGB roundtrip");
        check(vr444::pack(frame)==vr444::pack(restored),"coefficient roundtrip");check(wire.size()<=vr444::pack(frame).size()+4,"fallback expansion");
        auto fast=encode(frame,nullptr,false);check(vr444::pack(frame)==vr444::pack(decode(fast)),"DC-only exact roundtrip");
    }
    auto source=vr444::scene(17,19,1);std::vector<uint32_t> tile_q{1,8,17,1024};
    auto frame=vr444::encode(source,17,19,8,tile_q);auto wire=encode(frame);
    check(vr444::pack(frame)==vr444::pack(decode(wire)),"quantizer map roundtrip");
    for(size_t n=0;n<wire.size();++n){bool failed=false;try{decode(std::span(wire).first(n));}catch(const std::runtime_error&){failed=true;}check(failed,"truncation accepted");}
    auto reject=[&](std::vector<uint8_t> bad){bool failed=false;try{decode(bad);}catch(const std::runtime_error&){failed=true;}check(failed,"malformed accepted");};
    auto bad=wire;bad.push_back(0);reject(bad);
    for(unsigned at:{0u,4u,8u,12u,16u,20u,24u}){bad=wire;for(unsigned i=0;i<4;++i)bad[at+i]=255;reject(bad);}
    bad=wire;bad[28]|=7;reject(bad); // Reserved tile mode.
    bad=wire;bad[28]=0;bad[29]=0;reject(bad); // Zero payload length.
    bad=wire;bad[30]=0;bad[31]=0;reject(bad); // Zero per-tile quantizer.
    auto solid=vr444::encode(std::vector<uint32_t>(256,0xff123456),16,16,1);auto solidwire=encode(solid);
    bad=solidwire;bad.back()|=128;reject(bad); // Nonzero DC padding.
    std::mt19937 random(947);for(unsigned i=0;i<3000;++i){bad=wire;bad[random()%bad.size()]^=uint8_t(1u<<(random()%8));try{vr444::decode(decode(bad));}catch(const std::runtime_error&) {}}
}
}
