// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <span>
#include <stdexcept>
#include <string>
#include <vector>
namespace wpp {
using Bytes=std::vector<uint8_t>; using View=std::span<const uint8_t>;
constexpr size_t header=40, max_record=1200, shard=max_record-header, max_pair=256*1024;
inline void need(bool b,const char*m){if(!b)throw std::runtime_error(m);}
inline uint64_t get(View b,size_t at,unsigned n){need(at<=b.size()&&n<=b.size()-at,"truncated integer");uint64_t v=0;for(unsigned i=0;i<n;++i)v|=uint64_t(b[at+i])<<(8*i);return v;}
inline void put(Bytes&b,size_t at,uint64_t v,unsigned n){need(at<=b.size()&&n<=b.size()-at,"integer output bound");for(unsigned i=0;i<n;++i)b[at+i]=uint8_t(v>>(8*i));}
struct Config {std::array<uint8_t,16>token{};uint32_t frames=0,pair_bytes=0,hz=0;};
inline uint32_t count(const Config&c){return (c.pair_bytes+shard-1)/shard;}
inline uint64_t nominal_ipv4_udp_bytes(const Config&c){return c.pair_bytes+count(c)*(header+28)+(header+16+28);}
inline void validate(const Config&c){need(std::any_of(c.token.begin(),c.token.end(),[](auto v){return v!=0;}),"zero token");need(c.frames>=1&&c.frames<=180&&c.pair_bytes>=2&&c.pair_bytes<=max_pair&&c.hz>=5&&c.hz<=90,"configuration bound");need(c.frames<=c.hz*8,"maximum nominal duration eight seconds");need(nominal_ipv4_udp_bytes(c)*8*c.hz<=100000000,"nominal IPv4 UDP offered rate exceeds 100Mbps");}
inline std::array<uint8_t,16>token(const std::string&s){need(s.size()==32,"token must have 32 hex digits");std::array<uint8_t,16>a{};auto hex=[](char c)->unsigned{if(c>='0'&&c<='9')return c-'0';if(c>='a'&&c<='f')return c-'a'+10;if(c>='A'&&c<='F')return c-'A'+10;throw std::runtime_error("token hex digit");};for(unsigned i=0;i<16;++i)a[i]=uint8_t(hex(s[2*i])*16+hex(s[2*i+1]));return a;}
inline uint64_t mix(uint64_t x){x+=0x9e3779b97f4a7c15ULL;x=(x^(x>>30))*0xbf58476d1ce4e5b9ULL;x=(x^(x>>27))*0x94d049bb133111ebULL;return x^(x>>31);}
// Identical opaque synthetic stereo bytes in TCP and UDP. The second half is
// a distinct eye domain; no codec, pixels, compression or source freshness claim.
inline Bytes pair(const Config&c,uint32_t frame){need(frame>=1&&frame<=c.frames,"frame bound");Bytes b(c.pair_bytes);uint64_t seed=get(c.token,0,8)^get(c.token,8,8)^uint64_t(frame)*0x100000001b3ULL;size_t split=b.size()/2;
 for(size_t i=0;i<b.size();++i){bool right=i>=split;size_t local=right?i-split:i;auto word=mix(seed^(right?0xabcdef0123456789ULL:0x123456789abcdef0ULL)^(local/8));b[i]=uint8_t(word>>(8*(local%8)));}return b;}
inline Bytes base(const Config&c,uint32_t frame,unsigned kind,unsigned index,size_t body){Bytes b(header+body,0);b[0]='W';b[1]='P';b[2]='P';b[3]='1';b[4]=1;b[5]=uint8_t(kind);std::copy(c.token.begin(),c.token.end(),b.begin()+8);put(b,24,frame,4);put(b,28,c.pair_bytes,4);put(b,32,index,2);put(b,34,count(c),2);put(b,36,body,2);return b;}
inline Bytes data(const Config&c,uint32_t frame,unsigned index,View source){need(source.size()==c.pair_bytes&&index<count(c),"source shape");size_t offset=size_t(index)*shard,n=std::min(shard,source.size()-offset);auto b=base(c,frame,1,index,n);std::copy_n(source.begin()+offset,n,b.begin()+header);return b;}
struct Record {unsigned kind,index;uint32_t frame;View body;};
inline Record parse(const Config&c,View b){need(b.size()>=header&&b.size()<=max_record,"record length");need(b[0]=='W'&&b[1]=='P'&&b[2]=='P'&&b[3]=='1'&&b[4]==1&&(b[5]==1||b[5]==2)&&get(b,6,2)==0&&get(b,38,2)==0,"record header");need(std::equal(c.token.begin(),c.token.end(),b.begin()+8),"run token");auto f=uint32_t(get(b,24,4));auto index=unsigned(get(b,32,2));need(f>=1&&f<=c.frames&&get(b,28,4)==c.pair_bytes&&get(b,34,2)==count(c)&&get(b,36,2)==b.size()-header,"record identity");if(b[5]==1){need(index<count(c),"record index");need(b.size()-header==std::min(shard,size_t(c.pair_bytes)-index*shard),"canonical shard length");}else need(index==0&&b.size()==header+16,"ACK shape");return {b[5],index,f,b.subspan(header)};}
struct Assembly {const Config&c;uint32_t frame;Bytes expected,received;std::vector<bool>seen;unsigned unique=0,duplicates=0;
 Assembly(const Config&cfg,uint32_t f):c(cfg),frame(f),expected(pair(c,f)),received(c.pair_bytes),seen(count(c),false){}
 bool accept(const Record&r){need(r.kind==1&&r.frame==frame,"assembly identity");size_t offset=size_t(r.index)*shard;need(r.index<seen.size()&&offset+r.body.size()<=expected.size(),"assembly bound");need(std::equal(r.body.begin(),r.body.end(),expected.begin()+offset),"source byte mismatch");if(seen[r.index]){++duplicates;return unique==seen.size();}std::copy(r.body.begin(),r.body.end(),received.begin()+offset);seen[r.index]=true;++unique;if(unique==seen.size()){need(received==expected,"whole pair exactness");return true;}return false;}
};
inline Bytes ack(const Config&c,uint32_t frame,uint64_t assembly_ns){auto b=base(c,frame,2,0,16);put(b,header,assembly_ns,8);put(b,header+8,count(c),4);put(b,header+12,c.pair_bytes,4);return b;}
inline uint64_t check_ack(const Config&c,const Record&r,uint32_t f){need(r.kind==2&&r.frame==f&&get(r.body,8,4)==count(c)&&get(r.body,12,4)==c.pair_bytes,"ACK completeness");auto ns=get(r.body,0,8);need(ns<=100000000,"ACK assembly deadline");return ns;}
inline uint64_t hash(View b){uint64_t h=14695981039346656037ULL;for(auto x:b){h^=x;h*=1099511628211ULL;}return h;}
} // namespace wpp
