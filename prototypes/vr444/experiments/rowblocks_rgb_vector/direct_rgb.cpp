// SPDX-License-Identifier: MIT
// Experimental packed-RGB output derivative of latency_decode/direct.cpp.
// Framing delegates to the unchanged original validator; tile arithmetic stays exact.
#include "direct_rgb.hpp"
#include <algorithm>
#include <array>
#include <cstring>
#include <limits>
#include <stdexcept>
#if defined(__aarch64__)
#include <arm_neon.h>
#elif defined(__SSE2__)
#include <emmintrin.h>
#include <tmmintrin.h>
#endif
namespace directrgbvec {
static void require(bool ok,const char* reason){if(!ok)throw std::runtime_error(reason);}
static uint32_t u32(const uint8_t* p){return uint32_t(p[0])|(uint32_t(p[1])<<8)|(uint32_t(p[2])<<16)|(uint32_t(p[3])<<24);}
struct Reader {
 std::span<const uint8_t> wire;size_t at=0;
 uint32_t get(unsigned n){require(n<=wire.size()-at,"truncated frame");uint32_t v=0;for(unsigned i=0;i<n;++i)v|=uint32_t(wire[at++])<<(i*8);return v;}
 void skip(size_t n){require(n<=wire.size()-at,"truncated record");at+=n;}
};
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
const char* vector_backend(){return "aarch64-neon-inverse+rgb-store";}
#elif defined(__SSE2__)
using Vec=__m128i;
static Vec load(const int* p){return _mm_loadu_si128(reinterpret_cast<const Vec*>(p));}static void store(int* p,Vec v){_mm_storeu_si128(reinterpret_cast<Vec*>(p),v);}
static Vec add(Vec a,Vec b){return _mm_add_epi32(a,b);}static Vec sub(Vec a,Vec b){return _mm_sub_epi32(a,b);}static Vec floorhalf(Vec a){return _mm_srai_epi32(a,1);}
static Vec ziplo(Vec a,Vec b){return _mm_unpacklo_epi32(a,b);}static Vec ziphi(Vec a,Vec b){return _mm_unpackhi_epi32(a,b);}
const char* vector_backend(){return __builtin_cpu_supports("ssse3")?"x86-sse2-inverse+ssse3-rgb-store":"x86-sse2-inverse+scalar-rgb-store";}
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
#if defined(__aarch64__) || defined(__SSE2__)
static void colors(const int*coeff,unsigned i,Vec&r,Vec&g,Vec&b){
 auto yy=load(coeff+i),co=load(coeff+256+i),cg=load(coeff+512+i);
 auto t=sub(yy,floorhalf(cg));g=add(cg,t);b=sub(t,floorhalf(co));r=add(b,co);
}
#endif
#if defined(__SSE2__)
static Vec rgba4(Vec r,Vec g,Vec b){
 auto zero=_mm_setzero_si128();
 auto r8=_mm_packus_epi16(_mm_packs_epi32(r,zero),zero);
 auto g8=_mm_packus_epi16(_mm_packs_epi32(g,zero),zero);
 auto b8=_mm_packus_epi16(_mm_packs_epi32(b,zero),zero);
 return _mm_unpacklo_epi16(_mm_unpacklo_epi8(r8,g8),_mm_unpacklo_epi8(b8,_mm_set1_epi8(-1)));
}
__attribute__((target("ssse3"))) static void store4(uint8_t*out,Vec rgba){
 auto packed=_mm_shuffle_epi8(rgba,_mm_setr_epi8(0,1,2,4,5,6,8,9,10,12,13,14,-1,-1,-1,-1));
 _mm_storel_epi64(reinterpret_cast<__m128i*>(out),packed);
 uint32_t tail=uint32_t(_mm_cvtsi128_si32(_mm_srli_si128(packed,8)));std::memcpy(out+8,&tail,4);
}
#elif defined(__aarch64__)
static uint8x8_t pack8(Vec low,Vec high){return vqmovn_u16(vcombine_u16(vqmovun_s32(low),vqmovun_s32(high)));}
#endif
static void constant_row(uint8_t*out,unsigned count,uint32_t p,bool vector){
 unsigned x=0;
#if defined(__SSE2__)
 if(vector)for(;count-x>=4;x+=4)store4(out+x*3,_mm_set1_epi32(int(p)));
#elif defined(__aarch64__)
 if(vector){uint8x8x3_t rgb{{vdup_n_u8(uint8_t(p)),vdup_n_u8(uint8_t(p>>8)),vdup_n_u8(uint8_t(p>>16))}};
  for(;count-x>=8;x+=8)vst3_u8(out+x*3,rgb);}
#else
 (void)vector;
#endif
 for(;x<count;++x){out[3*x]=uint8_t(p);out[3*x+1]=uint8_t(p>>8);out[3*x+2]=uint8_t(p>>16);}
}
static void mixed_row(uint8_t*out,const int*coeff,unsigned row,unsigned count,bool vector){
 unsigned x=0;
#if defined(__SSE2__)
 if(vector)for(;count-x>=4;x+=4){Vec r,g,b;colors(coeff,row*16+x,r,g,b);store4(out+x*3,rgba4(r,g,b));}
#elif defined(__aarch64__)
 if(vector)for(;count-x>=8;x+=8){Vec r0,g0,b0,r1,g1,b1;colors(coeff,row*16+x,r0,g0,b0);colors(coeff,row*16+x+4,r1,g1,b1);
  uint8x8x3_t rgb{{pack8(r0,r1),pack8(g0,g1),pack8(b0,b1)}};vst3_u8(out+x*3,rgb);}
#else
 (void)vector;
#endif
 for(;x<count;++x){unsigned i=row*16+x;auto p=pixel(coeff[i],coeff[256+i],coeff[512+i]);out[3*x]=uint8_t(p);out[3*x+1]=uint8_t(p>>8);out[3*x+2]=uint8_t(p>>16);}
}
Stats decode(std::span<const uint8_t> wire,const Shape& shape,std::span<uint8_t> output,bool simd){
 // Do not trust caller-supplied shape: recheck framing (bounds pass is small).
 Shape s=preflight(wire);require(s.width==shape.width&&s.height==shape.height&&s.q==shape.q&&s.tiles==shape.tiles&&s.version==shape.version,"shape mismatch");require(output.size()==size_t(s.width)*s.height*3,"output length");
 auto a=reinterpret_cast<uintptr_t>(wire.data()),b=reinterpret_cast<uintptr_t>(output.data());require(a<=std::numeric_limits<uintptr_t>::max()-wire.size()&&b<=std::numeric_limits<uintptr_t>::max()-output.size_bytes(),"pointer range");require(a+wire.size()<=b||b+output.size_bytes()<=a,"input/output overlap");
 bool vector_stores=simd;
#if defined(__SSE2__)
 vector_stores=simd&&__builtin_cpu_supports("ssse3");
#endif
 Reader r{wire,24};Stats stats;
 for(unsigned tile=0;tile<s.tiles;++tile){unsigned len=r.get(2),q=s.version==2?r.get(2):s.q;const uint8_t* data=wire.data()+r.at;r.skip(len*4);unsigned bx=(tile%((s.width+15)/16))*16,by=(tile/((s.width+15)/16))*16,cw=std::min(16u,s.width-bx),ch=std::min(16u,s.height-by);
  if(len==3){auto a=u32(data),b=u32(data+4),c=u32(data+8);if(!(a&~0xf80007ffu)&&!(b&~0xffc0003fu)&&!(c&~1u)){int yy=unzz(a&2047),co=unzz(((a>>27)|(b<<5))&2047),cg=unzz(((b>>22)|(c<<10))&2047);require(yy>=0&&yy<=255&&std::abs(co)<=255&&std::abs(cg)<=255,"DC range");auto p=pixel(yy,co,cg);for(unsigned y=0;y<ch;++y)constant_row(output.data()+(size_t(by+y)*s.width+bx)*3,cw,p,vector_stores);++stats.constant_tiles;continue;}}
  ++stats.mixed_tiles;int coeff[768]={};Bits bits{data,len};
  for(unsigned c=0;c<3;++c){int* v=coeff+c*256;v[0]=unzz(bits.get(11));require(c?std::abs(v[0])<=255:v[0]>=0&&v[0]<=255,"DC range");for(unsigned base=1;base<256;base+=16)if(bits.get(1)){unsigned n=bits.get(4);require(n>=1&&n<=11,"bit width");unsigned mode=bits.get(2);require(mode<=2,"group mode");unsigned repeated=mode==0?bits.get(n):0,mask=mode==2?bits.get(16):0xffff;require(base!=241||!(mask&0x8000)||mode!=2,"sparse tail");for(unsigned i=base;i<std::min(base+16,256u);++i){unsigned value=mode==0?repeated:((mask&(1u<<(i-base)))?bits.get(n):0);v[i]=unzz(value)*int(q);require(std::abs(v[i])<=1536,"AC range");}}inverse(v,simd);}
  require((bits.bit+31)/32==len,"trailing tile data");while(bits.bit<len*32)require(bits.get(1)==0,"padding");
  for(unsigned y=0;y<ch;++y)mixed_row(output.data()+(size_t(by+y)*s.width+bx)*3,coeff,y,cw,vector_stores);
 }
 return stats;
}
}
