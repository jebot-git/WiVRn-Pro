// SPDX-License-Identifier: MIT
#include "codec.hpp"
#include <algorithm>
#include <cmath>
#include <limits>
#include <random>
#include <stdexcept>
#include <array>
namespace vr444 {
static void require(bool v, const char* m) { if (!v) throw std::runtime_error(m); }
static unsigned blocks(unsigned w, unsigned h) {
    require(w && h && w<=4096 && h<=4096 && uint64_t(w)*h<=8388608, "invalid dimensions");
    return ((w+15)/16)*((h+15)/16);
}
// Floor division is explicit; C++ signed right shift is not relied upon.
static int half(int x) { return x>=0 ? x/2 : -((-x+1)/2); }
static unsigned zz(int x) { return x>=0 ? unsigned(2*x) : unsigned(-2*x-1); }
static int unzz(unsigned x) { return (x&1) ? -int((x+1)/2) : int(x/2); }
static void transform(int* a, bool inverse) {
    int t[16];
    for(int n=inverse?2:16; inverse?n<=16:n>=2; n=inverse?n*2:n/2) {
        for(int axis=0;axis<2;axis++) {
            bool col=inverse ? axis==0 : axis==1;
            for(int line=0;line<n;line++) {
                auto pos=[&](int i){ return col ? i*16+line : line*16+i; };
                for(int i=0;i<n/2;i++) {
                    if(!inverse) {
                        int d=a[pos(2*i)]-a[pos(2*i+1)];
                        t[i]=a[pos(2*i+1)]+half(d); t[i+n/2]=d;
                    } else {
                        int d=a[pos(i+n/2)], b=a[pos(i)]-half(d);
                        t[2*i]=b+d; t[2*i+1]=b;
                    }
                }
                for(int i=0;i<n;i++) a[pos(i)]=t[i];
            }
        }
    }
}
struct Bits {
    uint32_t* p; unsigned size, bit=0;
    void put(unsigned v,unsigned n) {
        require(bit+n<=size*32,"slot overflow");
        for(unsigned i=0;i<n;i++,bit++) p[bit/32]|=((v>>i)&1)<<(bit%32);
    }
    unsigned get(unsigned n) {
        require(n<=16 && bit+n<=size*32,"truncated coefficient");
        const unsigned word=bit/32,shift=bit%32;
        unsigned value=p[word]>>shift;
        if(shift+n>32)value|=p[word+1]<<(32-shift);
        bit+=n;return value&((1u<<n)-1);
    }
};
Frame encode(const std::vector<uint32_t>& rgba,unsigned w,unsigned h,unsigned q,const std::vector<uint32_t>& tile_q) {
    unsigned count=blocks(w,h); require(q>=1 && q<=1024,"invalid q");
    require(rgba.size()==size_t(w)*h,"invalid input size");
    Frame f{w,h,q,std::vector<uint32_t>(size_t(count)*SLOT_WORDS),std::vector<uint32_t>(count)};
    require(tile_q.empty() || tile_q.size()==count,"invalid quantizer map");
    f.tile_q=tile_q;
    for(unsigned b=0;b<count;b++) {
        unsigned tq=tile_q.empty()?q:tile_q[b];require(tq>=1 && tq<=1024,"invalid tile q");
        int v[768]; unsigned bx=(b%((w+15)/16))*16, by=(b/((w+15)/16))*16;
        for(unsigned y=0;y<16;y++) for(unsigned x=0;x<16;x++) {
            uint32_t p=rgba[size_t(std::min(by+y,h-1))*w+std::min(bx+x,w-1)];
            int r=p&255,g=(p>>8)&255,blue=(p>>16)&255;
            int co=r-blue,t=blue+half(co),cg=g-t;
            v[y*16+x]=t+half(cg);v[256+y*16+x]=co;v[512+y*16+x]=cg;
        }
        Bits bits{f.slots.data()+size_t(b)*SLOT_WORDS,SLOT_WORDS};
        for(unsigned c=0;c<3;c++) {
            int* a=v+c*256; transform(a,false); bits.put(zz(a[0]),11);
            for(unsigned i=1;i<256;i++) a[i]=(a[i]<0?-1:1)*((std::abs(a[i])+int(tq)/2)/int(tq));
            for(unsigned base=1;base<256;base+=16) {
                unsigned end=std::min(base+16,256u), mx=0, n=0, mask=0, nonzero=0;
                bool same=true;
                for(unsigned i=base;i<end;i++) { mx=std::max(mx,zz(a[i])); same &= a[i]==a[base]; if(a[i]){mask|=1u<<(i-base);nonzero++;} }
                for(unsigned x=mx;x;x>>=1) n++;
                bits.put(n!=0,1);
                if(n) {
                    unsigned mode=same?0:((16+nonzero*n<(end-base)*n)?2:1);
                    bits.put(n,4);bits.put(mode,2);
                    if(mode==0)bits.put(zz(a[base]),n);
                    else {if(mode==2)bits.put(mask,16);for(unsigned i=base;i<end;i++)if(mode==1 || a[i])bits.put(zz(a[i]),n);}
                }
            }
        }
        f.lengths[b]=(bits.bit+31)/32;
    }
    return f;
}
static std::vector<uint32_t> decode_impl(const Frame& f,bool reconstruct) {
    unsigned count=blocks(f.width,f.height);
    require(f.q>=1 && f.q<=1024 && f.lengths.size()==count && f.slots.size()==size_t(count)*SLOT_WORDS,"invalid frame");
    require(f.tile_q.empty() || f.tile_q.size()==count,"invalid quantizer map");
    std::vector<uint32_t> rgba(reconstruct?size_t(f.width)*f.height:0);
    for(unsigned b=0;b<count;b++) {
        unsigned tq=f.tile_q.empty()?f.q:f.tile_q[b];require(tq>=1 && tq<=1024,"invalid tile q");
        require(f.lengths[b]>=3 && f.lengths[b]<=SLOT_WORDS,"invalid tile size");
        Bits bits{const_cast<uint32_t*>(f.slots.data()+size_t(b)*SLOT_WORDS),f.lengths[b]};
        int v[768]={};
        for(unsigned c=0;c<3;c++) {
            int* a=v+c*256; a[0]=unzz(bits.get(11));
            require((c==0 && a[0]>=0 && a[0]<=255) || (c && std::abs(a[0])<=255),"invalid DC");
            for(unsigned base=1;base<256;base+=16) if(bits.get(1)) {
                unsigned n=bits.get(4); require(n>=1 && n<=11,"invalid bit width");
                unsigned mode=bits.get(2);require(mode<=2,"invalid group mode");
                unsigned common=mode==0?bits.get(n):0,mask=mode==2?bits.get(16):0xffff;
                require(base!=241 || !(mask&0x8000) || mode!=2,"invalid sparse mask");
                for(unsigned i=base;i<std::min(base+16,256u);i++) {
                    unsigned value=mode==0?common:((mask&(1u<<(i-base)))?bits.get(n):0);
                    a[i]=unzz(value)*int(tq);
                    require(std::abs(a[i])<=1536,"invalid AC range");
                }
            }
            if(reconstruct)transform(a,true);
        }
        require((bits.bit+31)/32==f.lengths[b],"trailing tile data");
        while(bits.bit<f.lengths[b]*32) require(bits.get(1)==0,"nonzero padding");
        if(!reconstruct)continue;
        unsigned bx=(b%((f.width+15)/16))*16, by=(b/((f.width+15)/16))*16;
        for(unsigned y=0;y<16 && by+y<f.height;y++) for(unsigned x=0;x<16 && bx+x<f.width;x++) {
            unsigned i=y*16+x; int t=v[i]-half(v[512+i]),g=v[512+i]+t,blue=t-half(v[256+i]),r=blue+v[256+i];
            rgba[size_t(by+y)*f.width+bx+x]=uint32_t(std::clamp(r,0,255)) | (uint32_t(std::clamp(g,0,255))<<8) | (uint32_t(std::clamp(blue,0,255))<<16) | 0xff000000u;
        }
    }
    return rgba;
}
std::vector<uint32_t> decode(const Frame& f){return decode_impl(f,true);}
void validate(const Frame& f){decode_impl(f,false);}
size_t payload_bytes(const Frame& f) { size_t n=24+f.lengths.size()*(f.tile_q.empty()?2:4);for(auto x:f.lengths)n+=x*4;return n; }
std::vector<uint8_t> pack(const Frame& f) {
    // Only internally produced/validated frames should reach this function.
    require(f.lengths.size()==blocks(f.width,f.height) && f.slots.size()==f.lengths.size()*SLOT_WORDS,"invalid frame");
    require(f.q>=1 && f.q<=1024 && (f.tile_q.empty() || f.tile_q.size()==f.lengths.size()),"invalid quantizer map");
    std::vector<uint8_t> out;
    auto put=[&](uint32_t v,unsigned n){for(unsigned i=0;i<n;i++)out.push_back(uint8_t(v>>(8*i)));};
    put(0x34345256,4);put(f.tile_q.empty()?1:2,4);put(f.width,4);put(f.height,4);put(f.q,4);put(f.lengths.size(),4);
    for(size_t b=0;b<f.lengths.size();b++) {
        require(f.lengths[b]>=3 && f.lengths[b]<=SLOT_WORDS,"invalid tile length"); put(f.lengths[b],2);
        if(!f.tile_q.empty()){require(f.tile_q[b]>=1 && f.tile_q[b]<=1024,"invalid tile q");put(f.tile_q[b],2);}
        for(unsigned i=0;i<f.lengths[b];i++)put(f.slots[b*SLOT_WORDS+i],4);
    }
    return out;
}
Frame unpack(const std::vector<uint8_t>& wire) {
    size_t at=0;
    auto get=[&](unsigned n){require(n<=wire.size()-at,"truncated frame");uint32_t v=0;for(unsigned i=0;i<n;i++)v|=uint32_t(wire[at++])<<(8*i);return v;};
    require(get(4)==0x34345256,"invalid magic");unsigned version=get(4);require(version==1 || version==2,"invalid version");
    unsigned w=get(4),h=get(4),q=get(4),n=get(4);
    require(n==blocks(w,h) && q>=1 && q<=1024,"invalid header");
    // Validate all record bounds before allocating the expanded GPU slots.
    size_t records=at;
    for(unsigned b=0;b<n;b++) {unsigned len=get(2);if(version==2){unsigned tq=get(2);require(tq>=1 && tq<=1024,"invalid tile q");}require(len>=3 && len<=SLOT_WORDS && size_t(len)*4<=wire.size()-at,"invalid tile record");at+=len*4;}
    require(at==wire.size(),"trailing frame data"); at=records;
    Frame f{w,h,q,std::vector<uint32_t>(size_t(n)*SLOT_WORDS),std::vector<uint32_t>(n)};
    if(version==2)f.tile_q.resize(n);
    for(unsigned b=0;b<n;b++){f.lengths[b]=get(2);if(version==2)f.tile_q[b]=get(2);for(unsigned i=0;i<f.lengths[b];i++)f.slots[size_t(b)*SLOT_WORDS+i]=get(4);}
    return f;
}
std::vector<uint32_t> scene(unsigned w,unsigned h,unsigned kind) {
    std::vector<uint32_t> a(size_t(w)*h); std::mt19937 rng(42);
    for(unsigned y=0;y<h;y++)for(unsigned x=0;x<w;x++) {
        unsigned r,g,b;
        if(kind==0){r=x*255/std::max(1u,w-1);g=y*255/std::max(1u,h-1);b=(x+y)*255/std::max(1u,w+h-2);}
        else if(kind==1){bool line=(x%37==0 || y%29==0);r=line?255:30;g=line?16:40;b=line?32:50; if(y<h/5){r=(x&1)?255:0;g=(x&1)?0:130;b=(x&1)?0:255;}}
        else {unsigned p=rng();r=p&255;g=(p>>8)&255;b=(p>>16)&255;}
        a[size_t(y)*w+x]=r|(g<<8)|(b<<16)|0xff000000u;
    }
    return a;
}
double psnr(const std::vector<uint32_t>& a,const std::vector<uint32_t>& b) {
    require(a.size()==b.size(),"PSNR size");double sum=0;
    for(size_t i=0;i<a.size();i++)for(unsigned c=0;c<3;c++){double d=int((a[i]>>(8*c))&255)-int((b[i]>>(8*c))&255);sum+=d*d;}
    return sum ? 10*std::log10(65025.0*a.size()*3/sum) : 999;
}
void self_test() {
    for(auto dims: {std::array<unsigned,2>{1,1},{17,19},{64,64}})for(unsigned kind=0;kind<3;kind++) {
        auto a=scene(dims[0],dims[1],kind);
        for(unsigned q:{1u,2u,17u,256u,1024u}) {
            auto f=encode(a,dims[0],dims[1],q);auto wire=pack(f);auto r=unpack(wire);auto out=decode(r);
            require(pack(r)==wire,"wire roundtrip");if(q==1)require(out==a,"lossless RGB444 roundtrip");
        }
    }
    auto a=scene(17,19,2);auto wire=pack(encode(a,17,19,4));
    for(size_t n=0;n<wire.size();n++) {bool failed=false;try{decode(unpack({wire.begin(),wire.begin()+n}));}catch(const std::runtime_error&){failed=true;}require(failed,"truncation accepted");}
    auto extra=wire;extra.push_back(0);bool failed=false;try{unpack(extra);}catch(const std::runtime_error&){failed=true;}require(failed,"trailing bytes accepted");
    // Bounded mutations need not be rejected if they describe a different valid frame.
    std::mt19937 rng(44);for(int n=0;n<2000;n++){auto bad=wire;bad[rng()%bad.size()]^=1u<<(rng()%8);try{decode(unpack(bad));}catch(const std::runtime_error&){} }
}
}
