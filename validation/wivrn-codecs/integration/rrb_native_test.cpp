// SPDX-License-Identifier: MIT
#include "codec_test/rrb/native.h"
#include "codec_test/vr444/codec.hpp"
#include <cassert>
#include <future>
#include <iostream>
using namespace wivrn::codec_test;
template<class F>void rejects(F f){bool bad=false;try{f();}catch(const std::exception&){bad=true;}assert(bad);}
int main(){
 for(auto shape: {std::pair{2u,2u},std::pair{17u,33u},std::pair{64u,65u}}){
  auto [w,h]=shape;std::array<std::vector<uint32_t>,2> pixels;
  std::array<rowblocks::Workspace,2> enc;std::array<std::vector<uint8_t>,2> wire;
  for(unsigned e=0;e<2;++e){pixels[e].resize(w*h);for(size_t i=0;i<pixels[e].size();++i)pixels[e][i]=0xff000000u|((i*71+e*90123)&0xffffff);auto b=rowblocks::encode(pixels[e],w,h,32,rowfilter::Policy::adaptive,enc[e]);wire[e].assign(b.begin(),b.end());}
  std::array<Header,2> headers{Header{Kind::rrb32,w,h,0,9,123},Header{Kind::rrb32,w,h,1,9,123}};
  for(unsigned workers:{2u,4u})for(bool residual:{false,true}){
   rrb::PairDecoder dec(workers,residual);
   for(unsigned repeat=0;repeat<5;++repeat){headers[0].frame=headers[1].frame=9+repeat;
    auto first=std::async(std::launch::async,[&]{return dec.acquire(headers[0],wire[0]);});
    auto right=dec.acquire(headers[1],wire[1]);auto left=first.get();assert(left==right&&left->frame==9+repeat);
    if(!residual)for(unsigned e=0;e<2;++e)assert(left->cpu.rgba[e]==pixels[e]);
    else for(unsigned e=0;e<2;++e){assert(left->index[e].block_rows==32);assert(left->residual[e].rgb.size()==size_t(w)*h*3);}
   }
  }
  // Band-view native per-eye adaptation equals canonical exact RRB bytes.
  rowbands::Workspace bands;rowworkers::Pool pool(4);
  for(unsigned e=0;e<2;++e)for(unsigned q:{1u,8u}){
   auto frame=vr444::encode(pixels[e],w,h,q);auto source=vr444::pack(frame);auto expected=vr444::decode(frame);
   rowblocks::Workspace ref;auto canonical=rowblocks::encode(expected,w,h,32,rowfilter::Policy::adaptive,ref);
   auto actual=rrb::transcode_eye(source,bands,pool);assert(std::equal(actual.begin(),actual.end(),canonical.begin(),canonical.end()));
  }
  auto corrupt=wire[1];corrupt.back()^=1;rrb::PairDecoder bad(2);
  auto first=std::async(std::launch::async,[&]{rejects([&]{bad.acquire(headers[0],wire[0]);});});
  rejects([&]{bad.acquire(headers[1],corrupt);});first.get();
  auto mismatch=headers;mismatch[1].warp++;rejects([&]{rrb::validate_pair(mismatch,{wire[0],wire[1]});});
  std::vector<uint8_t> huge(262144);rejects([&]{rrb::validate_pair(headers,{huge,wire[1]});});
  rrb::PairDecoder orphan(2);auto waiting=std::async(std::launch::async,[&]{rejects([&]{orphan.acquire(headers[0],wire[0]);});});
  orphan.stop();waiting.get();
 }
 rrb::Publication<int> pub;assert(!pub.arrive(0,1,std::make_shared<int>(1)));auto ready=pub.arrive(1,1,std::make_shared<int>(2));assert(ready&&*(*ready)[0]==1&&*(*ready)[1]==2);
 assert(!pub.arrive(1,2,nullptr));assert(!pub.arrive(0,2,std::make_shared<int>(3)));
 rejects([&]{pub.arrive(0,2,nullptr);});
 std::cout<<"native RRB32 exact source/band views, pair workers/residuals, malformed peer, cap, lifetime and stop checks passed\n";
}
