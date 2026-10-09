// SPDX-License-Identifier: MIT
#include "compact.hpp"
#include "intra_v2.hpp"
#include <algorithm>
#include <random>
#include <stdexcept>
namespace compact {
namespace {
void need(bool x,const char* text){if(!x)throw std::runtime_error(text);}
struct Bytes{std::span<const uint8_t> data;size_t at=0;
    uint32_t get(unsigned n){need(n<=4&&n<=data.size()-at,"short VRI2");uint32_t v=0;for(unsigned i=0;i<n;++i)v|=uint32_t(data[at++])<<(8*i);return v;}
    std::span<const uint8_t> take(size_t n){need(n<=data.size()-at,"short tile");auto r=data.subspan(at,n);at+=n;return r;}
};
struct Bits{std::span<const uint8_t> data;unsigned bit=0;
    unsigned get(unsigned n){need(n<=16&&bit+n<=data.size()*8,"short coefficients");unsigned v=0;for(unsigned shift=0;shift<n;){unsigned take=std::min(n-shift,8-bit%8);v|=((data[bit/8]>>(bit%8))&((1u<<take)-1))<<shift;bit+=take;shift+=take;}return v;}
    void end(unsigned alignment){need((bit+alignment-1)/alignment*alignment==data.size()*8,"extra tile bits");while(bit<data.size()*8)need(get(1)==0,"nonzero padding");}
};
int signed_value(unsigned v){return v&1?-int((v+1)/2):int(v/2);}
void grammar(std::span<const uint8_t> payload,unsigned mode,unsigned q){
    Bits bits{payload};
    if(mode==1){bits.get(8);need(bits.get(9)<=510&&bits.get(9)<=510,"constant DC range");bits.end(8);return;}
    for(unsigned c=0;c<3;++c){
        int dc=signed_value(bits.get(11));need(c?(dc>=-255&&dc<=255):(dc>=0&&dc<=255),"legacy DC range");
        for(unsigned base=1;base<256;base+=16){
            if(!bits.get(1))continue;
            unsigned width=bits.get(4),coding=bits.get(2);need(width>=1&&width<=11&&coding<=2,"legacy coding");
            unsigned repeated=coding==0?bits.get(width):0,mask=coding==2?bits.get(16):65535;
            need(base!=241||coding!=2||!(mask&32768),"legacy tail mask");
            for(unsigned i=base;i<std::min(base+16,256u);++i){unsigned v=coding==0?repeated:((mask&(1u<<(i-base)))?bits.get(width):0);need(std::abs(signed_value(v)*int(q))<=1536,"legacy AC range");}
        }
    }
    bits.end(32);
}
}
Frame prepare(std::span<const uint8_t> wire){
    // Maximum permitted frame cannot contain more than65536 tiles,1280 bytes
    // payload plus4 record bytes per tile and28-byte header.
    need(wire.size()<=28+size_t(65536)*1284,"frame byte cap");
    Bytes in{wire};need(in.get(4)==0x32495256&&in.get(4)==1,"VRI2 identity");
    unsigned w=in.get(4),h=in.get(4),q=in.get(4),count=in.get(4),flags=in.get(4);
    need(w&&h&&w<=4096&&h<=4096&&uint64_t(w)*h<=8388608,"VRI2 dimensions");
    need(count==((w+15)/16)*((h+15)/16)&&q>=1&&q<=1024&&flags<=1,"VRI2 header");
    // Preflight the complete packet before allocating wire/index staging.
    const size_t start=in.at;unsigned constants=0;
    for(unsigned tile=0;tile<count;++tile){
        unsigned tag=in.get(2),mode=tag&7,length=tag>>3,tq=flags?in.get(2):q;
        need(mode<=1&&tq>=1&&tq<=1024,"unsupported mode/q");
        need(mode==1?length==4:(length>=12&&length<=1280&&length%4==0),"tile length");
        grammar(in.take(length),mode,tq);constants+=mode==1;
    }
    need(in.at==wire.size(),"trailing bytes");
    Frame result{w,h,q,count,std::vector<uint8_t>(wire.begin(),wire.end()),{},constants};
    result.records.reserve(count);in.at=start;
    for(unsigned tile=0;tile<count;++tile){
        unsigned tag=in.get(2),mode=tag&7,length=tag>>3,tq=flags?in.get(2):q;
        result.records.push_back({uint32_t(in.at),length|(mode<<11)|(tq<<12)});in.take(length);
    }
    return result;
}
void self_test(){
    for(auto dims:{std::array<unsigned,2>{1,1},{17,19},{64,64}})for(unsigned kind=0;kind<3;++kind)for(unsigned q:{1u,8u,1024u}){
        auto frame=vr444::encode(vr444::scene(dims[0],dims[1],kind),dims[0],dims[1],q);
        auto wire=intra_v2::encode(frame,nullptr,false);auto parsed=prepare(wire);
        need(parsed.width==frame.width&&parsed.height==frame.height&&parsed.records.size()==frame.lengths.size(),"valid parse");
    }
    auto frame=vr444::encode(vr444::scene(17,19,1),17,19,8,{1,8,17,1024});auto wire=intra_v2::encode(frame,nullptr,false);
    need(prepare(wire).records.size()==4,"tile q parse");
    auto rejects=[](std::span<const uint8_t> bad){bool rejected=false;try{prepare(bad);}catch(const std::runtime_error&){rejected=true;}need(rejected,"accepted malformed input");};
    for(size_t n=0;n<wire.size();++n)rejects(std::span(wire).first(n));
    auto bad=wire;bad.push_back(0);rejects(bad);
    for(unsigned at:{0u,4u,8u,12u,16u,20u,24u}){bad=wire;for(unsigned j=0;j<4;++j)bad[at+j]=255;rejects(bad);}
    bad=wire;bad[28]=(bad[28]&248)|2;rejects(bad);
    bad=wire;bad[28]=bad[29]=0;rejects(bad);
    bad=wire;bad[30]=bad[31]=0;rejects(bad);
    auto solid=intra_v2::encode(vr444::encode(std::vector<uint32_t>(256,0xff123456),16,16,1),nullptr,false);
    bad=solid;bad.back()|=128;rejects(bad);
    std::mt19937 rng(88);for(unsigned i=0;i<3000;++i){bad=wire;bad[rng()%bad.size()]^=uint8_t(1u<<(rng()%8));Frame parsed{};bool accepted=true;try{parsed=prepare(bad);}catch(const std::runtime_error&){accepted=false;}if(accepted){auto reference=intra_v2::decode(bad);need(parsed.count==reference.lengths.size(),"mutation disagreement");}}
}
}
