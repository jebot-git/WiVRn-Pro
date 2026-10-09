// SPDX-License-Identifier: MIT
#include "encoder.hpp"
#include "codec.hpp"
#include <iostream>
#include <random>
using namespace rowrgbvec;
static size_t trips=0,rejections=0;
template<class F>static void reject(F f){bool caught=false;try{f();}catch(const std::runtime_error&){caught=true;}need(caught);++rejections;}
static void empty(const Workspace&w){for(unsigned e=0;e<2;++e)need(w.rgba[e].empty()&&w.blocks[e].empty()&&w.wire[e].empty());}
static void equal(View a,View b){need(a.size()==b.size()&&std::equal(a.begin(),a.end(),b.begin()));}
static size_t direct_rgb_cases=0;
static void check_direct_rgb(){
    std::mt19937 random(20261009);
    std::vector<std::pair<unsigned,unsigned>>shapes{{1,1},{17,19},{35,65},{64,17}};
    for(unsigned width=2;width<16;++width)shapes.emplace_back(width,17);
    for(auto [width,height]:shapes)
        for(unsigned kind=0;kind<4;++kind)for(unsigned q:{1u,8u,64u,1024u}){
            auto pixels=vr444::scene(width,height,kind%3);if(kind==3)for(auto&p:pixels)p=uint32_t(random())|0xff000000;
            auto wire=vr444::pack(vr444::encode(pixels,width,height,q));auto shape=direct::preflight(wire);
            auto reference=vr444::decode(vr444::unpack(wire));Bytes expected(reference.size()*3);
            for(size_t i=0;i<reference.size();++i)for(unsigned c=0;c<3;++c)expected[3*i+c]=uint8_t(reference[i]>>(8*c));
            std::vector<uint32_t>rgba(reference.size());auto original=direct::decode(wire,shape,rgba,true);need(rgba==reference);
            for(bool simd:{false,true}){Bytes guarded(expected.size()+2,0xa5);auto target=std::span(guarded).subspan(1,expected.size());
                auto stats=directrgbvec::decode(wire,shape,target,simd);equal(target,expected);need(guarded.front()==0xa5&&guarded.back()==0xa5);
                need(stats.constant_tiles==original.constant_tiles&&stats.mixed_tiles==original.mixed_tiles);
                for(auto policy:{rowfilter::Policy::plain,rowfilter::Policy::up,rowfilter::Policy::adaptive}){rowfilter::Workspace a,b;
                    equal(rgbvectorencode::encode(target,width,height,policy,a,simd),rowfilter::encode(reference,width,height,policy,b,simd));}
                ++direct_rgb_cases;
            }
            Bytes output(expected.size(),0x5a),old=output;auto wrong=shape;wrong.q=shape.q==1?8:1;
            reject([&]{directrgbvec::decode(wire,wrong,output,true);});need(output==old);
            reject([&]{directrgbvec::decode(wire,shape,std::span(output).first(output.size()-1),true);});need(output==old);
            Bytes overlap=wire;overlap.resize(std::max(wire.size(),expected.size()));Bytes before=overlap;
            reject([&]{directrgbvec::decode(View(overlap).first(wire.size()),shape,std::span(overlap).first(expected.size()),true);});need(overlap==before);
        }
    rowfilter::Workspace w;Bytes source{1,2,3};auto valid=rgbvectorencode::encode(source,1,1,rowfilter::Policy::up,w);Bytes retained(valid.begin(),valid.end());
    reject([&]{rgbvectorencode::encode(View(source).first(2),1,1,rowfilter::Policy::up,w);});equal(w.wire,retained);
    reject([&]{rgbvectorencode::encode(source,1,1,static_cast<rowfilter::Policy>(99),w);});equal(w.wire,retained);
    for(auto*buffer:{&w.rgb,&w.filtered,&w.compressed,&w.alternate}){buffer->assign(source.begin(),source.end());reject([&]{rgbvectorencode::encode(*buffer,1,1,rowfilter::Policy::up,w);});equal(w.wire,retained);}
    reject([&]{rgbvectorencode::encode(View(w.wire).first(3),1,1,rowfilter::Policy::up,w);});equal(w.wire,retained);
    // Grammar-aware differential corruption: the unchanged original decoder
    // supplies acceptance and byte expectations, with no failed image published.
    auto seed_pixels=vr444::scene(35,65,1);std::vector<uint32_t>qmap(15);
    for(size_t i=0;i<qmap.size();++i)qmap[i]=i%2?1:8;
    auto seed=vr444::pack(vr444::encode(seed_pixels,35,65,8,qmap));
    for(unsigned trial=0;trial<2000;++trial){Bytes wire=seed;wire[random()%wire.size()]^=uint8_t(1u<<(random()%8));
        bool original_ok=false,rgb_ok=false;std::vector<uint32_t>rgba;Bytes rgb;
        try{auto shape=direct::preflight(wire);rgba.resize(size_t(shape.width)*shape.height);direct::decode(wire,shape,rgba,true);original_ok=true;}catch(const std::runtime_error&){}
        try{auto shape=direct::preflight(wire);rgb.resize(size_t(shape.width)*shape.height*3);directrgbvec::decode(wire,shape,rgb,trial%2);rgb_ok=true;}catch(const std::runtime_error&){}
        need(original_ok==rgb_ok);
        if(original_ok){need(rgb.size()==rgba.size()*3);for(size_t i=0;i<rgba.size();++i)for(unsigned c=0;c<3;++c)need(rgb[i*3+c]==uint8_t(rgba[i]>>(8*c)));}
    }
}
int main(){try{
    check_direct_rgb();
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
    // A late malformed band must invalidate already completed private blocks.
    bad=l;size_t last=published.row_offsets[0][published.row_offsets[0].size()-2];
    for(unsigned column=1;column<(35+15)/16;++column){size_t words=size_t(bad[last])|size_t(bad[last+1])<<8;last+=4+words*4;}
    bad[last+4]=0xff;bad[last+5]=uint8_t((bad[last+5]&0xf8)|7);direct::preflight(bad);
    reject([&]{transcode_pair(bad,r,16,rowfilter::Policy::up,w,&four);});empty(w);retained();
    // All workspace-owned storage rejects before mutating a retained lease.
    for(unsigned eye=0;eye<2;++eye){reject([&]{transcode_pair(published.wire[eye],r,16,rowfilter::Policy::up,published,&four);});retained();
        reject([&]{transcode_pair(View(reinterpret_cast<const uint8_t*>(published.row_offsets[eye].data()),published.row_offsets[eye].size()*sizeof(size_t)),r,16,rowfilter::Policy::up,published,&four);});retained();
        auto&blocks=published.blocks[eye];reject([&]{transcode_pair(View(reinterpret_cast<const uint8_t*>(blocks.data()),blocks.size()*sizeof(rowfilter::Workspace)),r,16,rowfilter::Policy::up,published,&four);});retained();
        for(const auto*buffer:{&blocks[0].rgb,&blocks[0].filtered,&blocks[0].compressed,&blocks[0].wire})if(!buffer->empty()){
            reject([&]{transcode_pair(*buffer,r,16,rowfilter::Policy::up,published,&four);});retained();}
    }
    reject([&]{transcode_pair(View(reinterpret_cast<const uint8_t*>(&published),sizeof(published)),r,16,rowfilter::Policy::up,published,&four);});retained();
    for(const auto&scratch:published.scratch)if(!scratch.source_band.empty()){
        reject([&]{transcode_pair(scratch.source_band,r,16,rowfilter::Policy::up,published,&four);});retained();
        reject([&]{transcode_pair(View(scratch.rgb),r,16,rowfilter::Policy::up,published,&four);});retained();
        for(const auto*buffer:{&scratch.encoder.rgb,&scratch.encoder.filtered,&scratch.encoder.compressed,&scratch.encoder.wire})if(!buffer->empty()){reject([&]{transcode_pair(*buffer,r,16,rowfilter::Policy::up,published,&four);});retained();}
    }
    // Reuse the fused barrier and worker scratch over independent calls.
    for(unsigned i=0;i<300;++i){auto out=transcode_pair(l,r,16,rowfilter::Policy::up,w,&four);equal(out.left,old_left);equal(out.right,old_right);}
    // Maximum output dimensions and maximum512 block jobs with two distinct eyes.
    std::vector<uint32_t>maximum(rowfilter::max_pixels,0xff224466),maximum_right(maximum.size(),0xffaabbcc);
    auto lm=vr444::pack(vr444::encode(maximum,1024,4096,1)),rm=vr444::pack(vr444::encode(maximum_right,1024,4096,1));
    auto out=transcode_pair(lm,rm,16,rowfilter::Policy::up,w,&four);rowblocks::Workspace ref;auto exact=rowblocks::encode(maximum,1024,4096,16,rowfilter::Policy::up,ref);equal(out.left,exact);exact=rowblocks::encode(maximum_right,1024,4096,16,rowfilter::Policy::up,ref);equal(out.right,exact);++trips;
    // A legal8MP source is beyond the unchanged4MP output profile and rejects
    // before scratch reconstruction. Duplicate constant tile records are valid.
    Bytes oversized=lm;oversized.insert(oversized.end(),lm.begin()+24,lm.end());rowblocks::put32(oversized,8,2048);rowblocks::put32(oversized,20,32768);direct::preflight(oversized);
    reject([&]{transcode_pair(oversized,oversized,16,rowfilter::Policy::up,w,&four);});empty(w);
    // Maximum width and a partial129th row exercise long row-major record bands.
    for(unsigned height:{1u,129u}){auto a=vr444::scene(4096,height,1),b=vr444::scene(4096,height,0);auto wa=vr444::pack(vr444::encode(a,4096,height,8)),wb=vr444::pack(vr444::encode(b,4096,height,1));
        auto expected_a=vr444::decode(vr444::unpack(wa)),expected_b=vr444::decode(vr444::unpack(wb));rowblocks::Workspace txa,txb;
        for(unsigned rows:{16u,128u}){auto ea=rowblocks::encode(expected_a,4096,height,rows,rowfilter::Policy::up,txa),eb=rowblocks::encode(expected_b,4096,height,rows,rowfilter::Policy::up,txb);
            for(auto*pool:{static_cast<Pool*>(nullptr),&four}){auto pair=transcode_pair(wa,wb,rows,rowfilter::Policy::up,w,pool);equal(pair.left,ea);equal(pair.right,eb);need(w.rgba[0].empty()&&w.rgba[1].empty());++trips;}
        }
    }
    std::cout<<"{\"pass\":true,\"exact_canonical_stereo_cases\":"<<trips<<",\"direct_rgb_oracle_cases\":"<<direct_rgb_cases<<",\"differential_corruption_cases\":2000,\"rejections\":"<<rejections<<",\"worker_counts\":[1,2,4],\"reuse_batches\":300,\"max_block_jobs\":512,\"max_pixels_per_eye\":"<<rowfilter::max_pixels<<",\"source_qmaps_and_odd_dimensions\":true,\"separate_published_lease_retained\":true}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
