// SPDX-License-Identifier: MIT
#include "pair.hpp"
#include "../rowblocks_gpu/fixtures.hpp"
using namespace mapped_input;
using direct_image::require;
using rowfilter_gpu::Fixture;
static unsigned cases=0,rejections=0;
template<class F>void rejects(F&& fn){bool rejected=false;try{fn();}catch(const std::exception&){rejected=true;}require(rejected,"expected bounded admission/decode rejection");++rejections;}
void one(const Fixture& fixture,bool blocks){
    size_t raw=size_t(fixture.width)*fixture.height*3,padded=(raw+3)&~size_t(3);
    std::array<std::vector<uint8_t>,2> output{std::vector<uint8_t>(padded+32),std::vector<uint8_t>(padded+32)};
    std::array<Mutable,2> dest{Mutable(output[0]).subspan(16,padded),Mutable(output[1]).subspan(16,padded)};
    std::array<View,2> wires{fixture.wire[0],fixture.wire[1]};
    auto poison=[&]{for(auto& v:output)std::fill(v.begin(),v.end(),0x5a);};
    auto untouched=[&]{for(const auto& v:output)require(std::all_of(v.begin(),v.end(),[](auto b){return b==0x5a;}),"rejected admission changed output");};
    for(unsigned reuse=0;reuse<2;++reuse){poison();Pair pair(blocks,fixture.width,fixture.height,wires,dest);require(!pair.complete(),"preflight alone is not decoded");pair.decode();require(pair.complete(),"complete stereo decode");
        for(unsigned eye=0;eye<2;++eye){std::vector<uint8_t> reference;
            if(blocks){rowblocks::Workspace w;rowblocks::unpack_rgb(wires[eye],w);reference=std::move(w.rgb);}
            else{rowfilter::Workspace w;rowfilter::unpack_rgb(wires[eye],w);reference=std::move(w.rgb);}
            require(reference.size()==raw&&std::equal(reference.begin(),reference.end(),dest[eye].begin()),"mapped residual differs from independent workspace decode");
            require(std::all_of(dest[eye].begin()+raw,dest[eye].end(),[](auto b){return b==0;}),"rounded uint padding");
            require(std::all_of(output[eye].begin(),output[eye].begin()+16,[](auto b){return b==0x5a;})&&std::all_of(output[eye].end()-16,output[eye].end(),[](auto b){return b==0x5a;}),"destination canary");
        }
        rejects([&]{pair.decode();});require(!pair.complete(),"reused token retained stale readiness");
    }
    poison();auto bad=fixture.wire[1];bad.back()^=1;
    rejects([&]{Pair p(blocks,fixture.width,fixture.height,{wires[0],bad},dest);});untouched();
    auto short_dest=dest;short_dest[1]=short_dest[1].first(padded-1);
    rejects([&]{Pair p(blocks,fixture.width,fixture.height,wires,short_dest);});untouched();
    rejects([&]{Pair p(blocks,fixture.width,fixture.height,wires,{dest[0],dest[0]});});untouched();
    rejects([&]{Pair p(blocks,fixture.width==1?2:1,fixture.height,wires,dest);});untouched();
    // Back the aliased view with an actual large allocation; no fabricated
    // out-of-allocation span is needed even when compressed input is tiny.
    std::vector<uint8_t> aliased(std::max(padded,wires[0].size()),0x5a);std::copy(wires[0].begin(),wires[0].end(),aliased.begin());auto saved=aliased;
    View wire_alias(aliased.data(),wires[0].size());Mutable target_alias(aliased.data(),padded);
    rejects([&]{Pair p(blocks,fixture.width,fixture.height,{wire_alias,wires[1]},{target_alias,dest[1]});});
    rejects([&]{Pair p(blocks,fixture.width,fixture.height,{wire_alias,wires[1]},{dest[0],target_alias});});
    require(aliased==saved,"alias failure mutated source/output");untouched();
    Pair null_decoder(blocks,fixture.width,fixture.height,wires,dest);rejects([&]{null_decoder.decode(nullptr);});require(!null_decoder.complete(),"null decoder became ready");untouched();
    rejects([&]{null_decoder.decode();});require(!null_decoder.complete(),"failed token reused");untouched();++cases;
}
int fail_decode(const char*,char* output,int,int bytes){if(bytes)output[0]=char(0xee);return -1;}
void late_failure(){
    constexpr unsigned w=17,h=19;std::vector<uint32_t> pixels(w*h,0xff030201);rowfilter::Workspace workspace;
    auto compressed=rowfilter::encode(pixels,w,h,rowfilter::Policy::plain,workspace);require(rowfilter::inspect(compressed).mode!=0,"fault case must call LZ4");
    rowfilter::Bytes raw(rowfilter::header_bytes+w*h*3,0);raw[0]='R';raw[1]='R';raw[2]='F';raw[3]='1';raw[4]=1;
    rowfilter::put32(raw,8,w);rowfilter::put32(raw,12,h);rowfilter::put32(raw,16,w*h*3);rowfilter::put32(raw,20,w*h*3);
    for(size_t i=0;i<w*h;++i){raw[32+3*i]=1;raw[33+3*i]=2;raw[34+3*i]=3;}
    rowfilter::put32(raw,24,wpc::crc(View(raw).subspan(32)));rowfilter::put32(raw,28,wpc::crc(View(raw).first(28)));
    size_t size=(w*h*3+3)&~size_t(3);std::array<rowfilter::Bytes,2> output{rowfilter::Bytes(size,0x5a),rowfilter::Bytes(size,0x5a)};
    Pair p(false,w,h,{raw,compressed},{output[0],output[1]});rejects([&]{p.decode(fail_decode);});
    require(!p.complete()&&output[0][0]==1&&output[1][0]==0xee,"late right-eye failure did not withhold readiness");
    rejects([&]{p.decode();});require(!p.complete(),"late failed token reused");
    Pair recovery(false,w,h,{raw,compressed},{output[0],output[1]});recovery.decode();require(recovery.complete(),"new fully admitted pair recovers");
}
int main(int argc,char**argv){try{
    require(argc==5,"usage: mapped-pair-test RRF_GOLDENS RRF_CORPUS RRB_GOLDENS RRB_CORPUS");
    auto rrf=rowfilter_gpu::generated(true,true);rowfilter_gpu::append_corpus(rrf,argv[1],"golden");rowfilter_gpu::append_corpus(rrf,argv[2],"corpus");
    for(const auto& f:rrf)one(f,false);rrf.clear();rrf.shrink_to_fit();
    auto rrb=rowblocks_gpu::generated(true,true);rowblocks_gpu::append_corpus(rrb,argv[3],"golden");rowblocks_gpu::append_corpus(rrb,argv[4],"corpus");
    for(const auto& f:rrb)one(f,true);late_failure();
    std::cout<<"{\"pass\":true,\"cases\":"<<cases<<",\"reuse_rounds\":2,\"rejections\":"<<rejections<<",\"late_partial_decode_withheld\":true,\"vulkan_initialized\":false,\"headset_used\":false}\n";return 0;
}catch(const std::exception& e){std::cerr<<"mapped-pair-test: "<<e.what()<<'\n';return 1;}}
