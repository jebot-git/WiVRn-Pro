// SPDX-License-Identifier: MIT
#include "encoder.hpp"
#include "codec.hpp"
#include <iostream>
#include <random>
using namespace rowencoder;
static size_t trips=0,rejections=0;
template<class F>static void reject(F f){bool caught=false;try{f();}catch(const std::runtime_error&){caught=true;}need(caught);++rejections;}
static void empty(const Workspace&w){for(unsigned e=0;e<2;++e)need(w.rgba[e].empty()&&w.blocks[e].empty()&&w.wire[e].empty());}
static void equal(View a,View b){need(a.size()==b.size()&&std::equal(a.begin(),a.end(),b.begin()));}
int main(){try{
    Pool one(1),two(2),four(4);std::array<Pool*,4>pools{nullptr,&one,&two,&four};Workspace w;
    for(auto [width,height]:{std::pair{1u,1u},{1u,65u},{17u,19u},{35u,129u},{576u,640u}})
        for(unsigned kind=0;kind<3;++kind){auto left=vr444::scene(width,height,kind),right=left;for(auto&p:right)p=(p^0x007f3b29)|0xff000000;
            auto l=vr444::pack(vr444::encode(left,width,height,kind==0?1:8)),r=vr444::pack(vr444::encode(right,width,height,kind==0?8:1));
            auto decoded_left=vr444::decode(vr444::unpack(l)),decoded_right=vr444::decode(vr444::unpack(r));
            rowblocks::Workspace reference_left,reference_right,rx;
            for(uint32_t rows:{16,32,64,128})for(auto policy:{rowfilter::Policy::plain,rowfilter::Policy::up,rowfilter::Policy::adaptive}){
                auto a=rowblocks::encode(decoded_left,width,height,rows,policy,reference_left),b=rowblocks::encode(decoded_right,width,height,rows,policy,reference_right);
                for(auto*pool:pools){auto out=transcode_pair(l,r,rows,policy,w,pool);equal(out.left,a);equal(out.right,b);
                    std::vector<uint32_t>pixels(decoded_left.size());rowblocks::decode_into(out.left,pixels,rx);need(pixels==decoded_left);rowblocks::decode_into(out.right,pixels,rx);need(pixels==decoded_right);++trips;}
            }
        }
    // Independent v2 per-tile quantizers, odd dimensions and differing eye maps.
    auto pixels=vr444::scene(35,65,1),other=vr444::scene(35,65,0);std::vector<uint32_t>qmap(((35+15)/16)*((65+15)/16));
    for(size_t i=0;i<qmap.size();++i)qmap[i]=i%2?1:8;
    auto l=vr444::pack(vr444::encode(pixels,35,65,8,qmap));std::reverse(qmap.begin(),qmap.end());auto r=vr444::pack(vr444::encode(other,35,65,8,qmap));
    Workspace published;auto old=transcode_pair(l,r,16,rowfilter::Policy::up,published,&four);Bytes old_left(old.left.begin(),old.left.end()),old_right(old.right.begin(),old.right.end());
    auto retained=[&]{equal(old.left,old_left);equal(old.right,old_right);};
    for(auto*p:pools){auto out=transcode_pair(l,r,16,rowfilter::Policy::up,w,p);equal(out.left,old_left);equal(out.right,old_right);++trips;}
    for(size_t n=0;n<r.size();++n){reject([&]{transcode_pair(l,View(r).first(n),16,rowfilter::Policy::up,w,&four);});empty(w);retained();}
    for(auto rows:{0u,15u,256u}){reject([&]{transcode_pair(l,r,rows,rowfilter::Policy::up,w,&four);});empty(w);retained();}
    reject([&]{transcode_pair(l,r,16,static_cast<rowfilter::Policy>(99),w,&four);});empty(w);
    auto wrong=vr444::pack(vr444::encode(vr444::scene(35,64,0),35,64,1));reject([&]{transcode_pair(l,wrong,16,rowfilter::Policy::up,w,&four);});empty(w);
    // Invalid coefficient content can pass framing, fail in a worker after
    // another eye started, and still clear both private candidate outputs.
    Bytes bad=l;bad[28]=0xff;bad[29]=uint8_t((bad[29]&0xf8)|7);direct::preflight(bad);
    reject([&]{transcode_pair(bad,r,16,rowfilter::Policy::up,w,&four);});empty(w);retained();
    // All workspace-owned storage rejects before mutating a retained lease.
    for(unsigned eye=0;eye<2;++eye){reject([&]{transcode_pair(published.wire[eye],r,16,rowfilter::Policy::up,published,&four);});retained();
        reject([&]{transcode_pair(rowfilter::bytes(published.rgba[eye]),r,16,rowfilter::Policy::up,published,&four);});retained();
        auto&blocks=published.blocks[eye];reject([&]{transcode_pair(View(reinterpret_cast<const uint8_t*>(blocks.data()),blocks.size()*sizeof(rowfilter::Workspace)),r,16,rowfilter::Policy::up,published,&four);});retained();
        for(const auto*buffer:{&blocks[0].rgb,&blocks[0].filtered,&blocks[0].compressed,&blocks[0].wire})if(!buffer->empty()){
            reject([&]{transcode_pair(*buffer,r,16,rowfilter::Policy::up,published,&four);});retained();}
    }
    reject([&]{transcode_pair(View(reinterpret_cast<const uint8_t*>(&published),sizeof(published)),r,16,rowfilter::Policy::up,published,&four);});retained();
    // Reuse both barriers and allocations over many independently completed calls.
    for(unsigned i=0;i<300;++i){auto out=transcode_pair(l,r,16,rowfilter::Policy::up,w,&four);equal(out.left,old_left);equal(out.right,old_right);}
    // Maximum output dimensions and maximum512 block jobs with two distinct eyes.
    std::vector<uint32_t>maximum(rowfilter::max_pixels,0xff224466),maximum_right(maximum.size(),0xffaabbcc);
    auto lm=vr444::pack(vr444::encode(maximum,1024,4096,1)),rm=vr444::pack(vr444::encode(maximum_right,1024,4096,1));
    auto out=transcode_pair(lm,rm,16,rowfilter::Policy::up,w,&four);rowblocks::Workspace ref;auto exact=rowblocks::encode(maximum,1024,4096,16,rowfilter::Policy::up,ref);equal(out.left,exact);exact=rowblocks::encode(maximum_right,1024,4096,16,rowfilter::Policy::up,ref);equal(out.right,exact);++trips;
    std::cout<<"{\"pass\":true,\"exact_canonical_stereo_cases\":"<<trips<<",\"rejections\":"<<rejections<<",\"worker_counts\":[1,2,4],\"reuse_batches\":300,\"max_block_jobs\":512,\"max_pixels_per_eye\":"<<rowfilter::max_pixels<<",\"source_qmaps_and_odd_dimensions\":true,\"separate_published_lease_retained\":true}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
