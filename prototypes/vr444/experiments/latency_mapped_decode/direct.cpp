// SPDX-License-Identifier: MIT
#include "direct.hpp"
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <stdexcept>
#if defined(__aarch64__)
#include <arm_neon.h>
#elif defined(__SSE2__)
#include <emmintrin.h>
#endif
namespace direct {
static void require(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
static uint32_t u32(const uint8_t* p){return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);}
struct Reader {
 std::span<const uint8_t> wire;size_t at=0;
 uint32_t get(unsigned n){require(n<=wire.size()-at,"truncated frame");uint32_t v=0;for(unsigned i=0;i<n;++i)v|=uint32_t(wire[at++])<<(i*8);return v;}
 void skip(size_t n){require(n<=wire.size()-at,"truncated record");at+=n;}
};
Shape preflight(std::span<const uint8_t> wire){
 require(wire.size()<=16*1024*1024,"wire size bound");Reader r{wire};require(r.get(4)==0x34345256,"magic");Shape s{};s.version=r.get(4);require(s.version==1||s.version==2,"version");s.width=r.get(4);s.height=r.get(4);s.q=r.get(4);s.tiles=r.get(4);
 require(s.width&&s.height&&s.width<=4096&&s.height<=4096&&uint64_t(s.width)*s.height<=8388608,"dimensions");
 require(s.q>=1&&s.q<=1024&&s.tiles==((s.width+15)/16)*((s.height+15)/16),"header");
 for(unsigned tile=0;tile<s.tiles;++tile){unsigned len=r.get(2);if(s.version==2){auto q=r.get(2);require(q>=1&&q<=1024,"tile q");}require(len>=3&&len<=320,"tile length");r.skip(len*4);}
 require(r.at==wire.size(),"trailing data");return s;
}
static int half(int x){return x>=0?x/2:-((-x+1)/2);}
static int unzz(unsigned x){return (x&1)?-int((x+1)/2):int(x/2);}
struct Bits {
 const uint8_t* data;unsigned words,bit=0;
 uint32_t get(unsigned n){require(n<=16 && bit+n<=words*32,"coefficient bits");unsigned word=bit/32,shift=bit%32;uint32_t value=u32(data+word*4)>>shift;if(shift+n>32)value|=u32(data+(word+1)*4)<<(32-shift);bit+=n;return value&((1u<<n)-1);}
};
#if defined(__aarch64__)
using Vec=int32x4_t;
static Vec load(const int* p){return vld1q_s32(p);}static void store(int* p,Vec v){vst1q_s32(p,v);}
static Vec add(Vec a,Vec b){return vaddq_s32(a,b);}static Vec sub(Vec a,Vec b){return vsubq_s32(a,b);}static Vec floorhalf(Vec a){return vshrq_n_s32(a,1);}
static Vec ziplo(Vec a,Vec b){return vzip1q_s32(a,b);}static Vec ziphi(Vec a,Vec b){return vzip2q_s32(a,b);}
const char* vector_backend(){return "aarch64-neon";}
#elif defined(__SSE2__)
using Vec=__m128i;
static Vec load(const int* p){return _mm_loadu_si128(reinterpret_cast<const Vec*>(p));}static void store(int* p,Vec v){_mm_storeu_si128(reinterpret_cast<Vec*>(p),v);}
static Vec add(Vec a,Vec b){return _mm_add_epi32(a,b);}static Vec sub(Vec a,Vec b){return _mm_sub_epi32(a,b);}static Vec floorhalf(Vec a){return _mm_srai_epi32(a,1);}
static Vec ziplo(Vec a,Vec b){return _mm_unpacklo_epi32(a,b);}static Vec ziphi(Vec a,Vec b){return _mm_unpackhi_epi32(a,b);}
const char* vector_backend(){return "x86-sse2";}
#else
const char* vector_backend(){return "scalar-fallback";}
#endif
static void inverse(int* v,bool simd){
 int scratch[256];
 for(int n=2;n<=16;n*=2){
  // Column inverse v -> scratch, then row inverse scratch -> v. No intermediate
  // shared-memory copies/barriers, no temporary expanded 320-word tile slots.
  for(int i=0;i<n/2;++i){int x=0;
#if defined(__aarch64__) || defined(__SSE2__)
   if(simd)for(;x+4<=n;x+=4){Vec d=load(v+(i+n/2)*16+x),b=sub(load(v+i*16+x),floorhalf(d));store(scratch+2*i*16+x,add(b,d));store(scratch+(2*i+1)*16+x,b);}
#else
   (void)simd;
#endif
   for(;x<n;++x){int d=v[(i+n/2)*16+x],b=v[i*16+x]-half(d);scratch[2*i*16+x]=b+d;scratch[(2*i+1)*16+x]=b;}
  }
  for(int y=0;y<n;++y){int i=0;
#if defined(__aarch64__) || defined(__SSE2__)
   if(simd)for(;i+4<=n/2;i+=4){Vec d=load(scratch+y*16+i+n/2),b=sub(load(scratch+y*16+i),floorhalf(d)),a=add(b,d);store(v+y*16+2*i,ziplo(a,b));store(v+y*16+2*i+4,ziphi(a,b));}
#endif
   for(;i<n/2;++i){int d=scratch[y*16+i+n/2],b=scratch[y*16+i]-half(d);v[y*16+2*i]=b+d;v[y*16+2*i+1]=b;}
  }
 }
}
static uint32_t pixel(int yy,int co,int cg){int t=yy-half(cg),g=cg+t,b=t-half(co),r=b+co;return uint32_t(std::clamp(r,0,255))|(uint32_t(std::clamp(g,0,255))<<8)|(uint32_t(std::clamp(b,0,255))<<16)|0xff000000u;}
Stats decode(std::span<const uint8_t> wire,const Shape& shape,std::span<uint32_t> output,bool simd){
 // Do not trust caller-supplied shape: recheck framing (bounds pass is small).
 Shape s=preflight(wire);require(s.width==shape.width&&s.height==shape.height&&s.q==shape.q&&s.tiles==shape.tiles&&s.version==shape.version,"shape mismatch");require(output.size()==size_t(s.width)*s.height,"output length");
 auto a=reinterpret_cast<uintptr_t>(wire.data()),b=reinterpret_cast<uintptr_t>(output.data());require(a<=std::numeric_limits<uintptr_t>::max()-wire.size()&&b<=std::numeric_limits<uintptr_t>::max()-output.size_bytes(),"pointer range");require(a+wire.size()<=b||b+output.size_bytes()<=a,"input/output overlap");
 Reader r{wire,24};Stats stats;
 for(unsigned tile=0;tile<s.tiles;++tile){unsigned len=r.get(2),q=s.version==2?r.get(2):s.q;const uint8_t* data=wire.data()+r.at;r.skip(len*4);unsigned bx=(tile%((s.width+15)/16))*16,by=(tile/((s.width+15)/16))*16,cw=std::min(16u,s.width-bx),ch=std::min(16u,s.height-by);
  if(len==3){auto a=u32(data),b=u32(data+4),c=u32(data+8);if(!(a&~0xf80007ffu)&&!(b&~0xffc0003fu)&&!(c&~1u)){int yy=unzz(a&2047),co=unzz(((a>>27)|(b<<5))&2047),cg=unzz(((b>>22)|(c<<10))&2047);require(yy>=0&&yy<=255&&std::abs(co)<=255&&std::abs(cg)<=255,"DC range");auto p=pixel(yy,co,cg);for(unsigned y=0;y<ch;++y)std::fill_n(output.data()+size_t(by+y)*s.width+bx,cw,p);++stats.constant_tiles;continue;}}
  ++stats.mixed_tiles;int coeff[768]={};Bits bits{data,len};
  for(unsigned c=0;c<3;++c){int* v=coeff+c*256;v[0]=unzz(bits.get(11));require(c?std::abs(v[0])<=255:v[0]>=0&&v[0]<=255,"DC range");for(unsigned base=1;base<256;base+=16)if(bits.get(1)){unsigned n=bits.get(4);require(n>=1&&n<=11,"bit width");unsigned mode=bits.get(2);require(mode<=2,"group mode");unsigned repeated=mode==0?bits.get(n):0,mask=mode==2?bits.get(16):0xffff;require(base!=241||!(mask&0x8000)||mode!=2,"sparse tail");for(unsigned i=base;i<std::min(base+16,256u);++i){unsigned value=mode==0?repeated:((mask&(1u<<(i-base)))?bits.get(n):0);v[i]=unzz(value)*int(q);require(std::abs(v[i])<=1536,"AC range");}}inverse(v,simd);}
  require((bits.bit+31)/32==len,"trailing tile data");while(bits.bit<len*32)require(bits.get(1)==0,"padding");
  for(unsigned y=0;y<ch;++y)for(unsigned x=0;x<cw;++x){unsigned i=y*16+x;output[size_t(by+y)*s.width+bx+x]=pixel(coeff[i],coeff[256+i],coeff[512+i]);}
 }
 return stats;
}
}
