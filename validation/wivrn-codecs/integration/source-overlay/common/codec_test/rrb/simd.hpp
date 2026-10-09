// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include <cstring>
#include <span>
#include <stdexcept>
#if defined(__aarch64__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
#include <arm_neon.h>
#elif defined(__SSE2__)
#include <emmintrin.h>
#include <tmmintrin.h>
#endif

namespace rowfilter::detail {
inline void pack_scalar(std::span<const uint32_t>rgba,std::span<uint8_t>rgb){
    for(size_t i=0;i<rgba.size();++i){uint32_t p=rgba[i];if((p>>24)!=255)throw std::runtime_error("non-opaque RGB input");
        rgb[3*i]=uint8_t(p);rgb[3*i+1]=uint8_t(p>>8);rgb[3*i+2]=uint8_t(p>>16);}
}
inline void expand_scalar(std::span<const uint8_t>rgb,std::span<uint32_t>rgba){
    for(size_t i=0;i<rgba.size();++i)rgba[i]=uint32_t(rgb[3*i])|(uint32_t(rgb[3*i+1])<<8)|(uint32_t(rgb[3*i+2])<<16)|0xff000000u;
}
#if defined(__SSE2__)
inline bool ssse3(){static bool supported=__builtin_cpu_supports("ssse3");return supported;}
__attribute__((target("ssse3"))) inline void pack_ssse3(std::span<const uint32_t>rgba,std::span<uint8_t>rgb){
    const auto mask=_mm_setr_epi8(0,1,2,4,5,6,8,9,10,12,13,14,-1,-1,-1,-1);const auto alpha=_mm_set1_epi32(255);size_t i=0;
    for(;rgba.size()-i>=4;i+=4){auto p=_mm_loadu_si128(reinterpret_cast<const __m128i*>(rgba.data()+i));
        if(_mm_movemask_epi8(_mm_cmpeq_epi32(_mm_srli_epi32(p,24),alpha))!=0xffff)throw std::runtime_error("non-opaque RGB input");
        auto v=_mm_shuffle_epi8(p,mask);_mm_storel_epi64(reinterpret_cast<__m128i*>(rgb.data()+3*i),v);
        uint32_t tail=uint32_t(_mm_cvtsi128_si32(_mm_srli_si128(v,8)));std::memcpy(rgb.data()+3*i+8,&tail,4);
    }
    pack_scalar(rgba.subspan(i),rgb.subspan(3*i));
}
__attribute__((target("ssse3"))) inline void expand_ssse3(std::span<const uint8_t>rgb,std::span<uint32_t>rgba){
    const auto mask=_mm_setr_epi8(0,1,2,-1,3,4,5,-1,6,7,8,-1,9,10,11,-1);const auto alpha=_mm_set1_epi32(int(0xff000000u));size_t i=0;
    // A16-byte load reads12 useful bytes plus4 lookahead bytes. Six remaining
    // RGB pixels guarantee the whole load is inside the logical source extent.
    for(;rgba.size()-i>=6;i+=4){auto p=_mm_loadu_si128(reinterpret_cast<const __m128i*>(rgb.data()+3*i));
        _mm_storeu_si128(reinterpret_cast<__m128i*>(rgba.data()+i),_mm_or_si128(_mm_shuffle_epi8(p,mask),alpha));}
    expand_scalar(rgb.subspan(3*i),rgba.subspan(i));
}
#endif
inline const char* backend(){
#if defined(__aarch64__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    return "aarch64-neon";
#elif defined(__SSE2__)
    return ssse3()?"x86-ssse3-pack-expand+sse2-row":"x86-sse2-row+scalar-pack-expand";
#else
    return "scalar-fallback";
#endif
}
inline void pack(std::span<const uint32_t>rgba,std::span<uint8_t>rgb,bool vector){
    if(!vector){pack_scalar(rgba,rgb);return;}
#if defined(__aarch64__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    size_t i=0;for(;rgba.size()-i>=16;i+=16){auto p=vld4q_u8(reinterpret_cast<const uint8_t*>(rgba.data()+i));
        if(vminvq_u8(p.val[3])!=255)throw std::runtime_error("non-opaque RGB input");
        uint8x16x3_t out{{p.val[0],p.val[1],p.val[2]}};vst3q_u8(rgb.data()+3*i,out);}
    pack_scalar(rgba.subspan(i),rgb.subspan(3*i));
#elif defined(__SSE2__)
    if(ssse3())pack_ssse3(rgba,rgb);else pack_scalar(rgba,rgb);
#else
    pack_scalar(rgba,rgb);
#endif
}
inline void expand(std::span<const uint8_t>rgb,std::span<uint32_t>rgba,bool vector){
    if(!vector){expand_scalar(rgb,rgba);return;}
#if defined(__aarch64__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
    size_t i=0;for(;rgba.size()-i>=16;i+=16){auto p=vld3q_u8(rgb.data()+3*i);uint8x16x4_t out{{p.val[0],p.val[1],p.val[2],vdupq_n_u8(255)}};
        vst4q_u8(reinterpret_cast<uint8_t*>(rgba.data()+i),out);}
    expand_scalar(rgb.subspan(3*i),rgba.subspan(i));
#elif defined(__SSE2__)
    if(ssse3())expand_ssse3(rgb,rgba);else expand_scalar(rgb,rgba);
#else
    expand_scalar(rgb,rgba);
#endif
}
inline size_t subtract(std::span<const uint8_t>current,std::span<const uint8_t>prior,std::span<uint8_t>out,bool vector){
    size_t at=0;
    if(vector){
#if defined(__aarch64__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
        for(;current.size()-at>=16;at+=16)vst1q_u8(out.data()+at,vsubq_u8(vld1q_u8(current.data()+at),vld1q_u8(prior.data()+at)));
#elif defined(__SSE2__)
        for(;current.size()-at>=16;at+=16){auto a=_mm_loadu_si128(reinterpret_cast<const __m128i*>(current.data()+at)),b=_mm_loadu_si128(reinterpret_cast<const __m128i*>(prior.data()+at));
            _mm_storeu_si128(reinterpret_cast<__m128i*>(out.data()+at),_mm_sub_epi8(a,b));}
#endif
    }
    for(;at<current.size();++at)out[at]=uint8_t(current[at]-prior[at]);
    return at;
}
inline void add_row(std::span<uint8_t>current,std::span<const uint8_t>prior,bool vector){
    size_t at=0;
    if(vector){
#if defined(__aarch64__) && __BYTE_ORDER__ == __ORDER_LITTLE_ENDIAN__
        for(;current.size()-at>=16;at+=16)vst1q_u8(current.data()+at,vaddq_u8(vld1q_u8(current.data()+at),vld1q_u8(prior.data()+at)));
#elif defined(__SSE2__)
        for(;current.size()-at>=16;at+=16){auto a=_mm_loadu_si128(reinterpret_cast<const __m128i*>(current.data()+at)),b=_mm_loadu_si128(reinterpret_cast<const __m128i*>(prior.data()+at));
            _mm_storeu_si128(reinterpret_cast<__m128i*>(current.data()+at),_mm_add_epi8(a,b));}
#endif
    }
    for(;at<current.size();++at)current[at]=uint8_t(current[at]+prior[at]);
}
}
