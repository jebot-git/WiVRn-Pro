// SPDX-License-Identifier: MIT
#include "dictionary.hpp"
#include <iostream>
#include <random>
using namespace dictionary;
static size_t trips=0,rejections=0,changes=0;
template<class Fn>static void reject(Fn fn){bool bad=false;try{fn();}catch(const std::runtime_error&){bad=true;}need(bad);++rejections;}
static void repair(Bytes&b){set32(b,60,wpc::crc(View(b).first(60)));}
int main(){try{
    Bytes hash_input{'1','2','3','4','5','6','7','8','9'};Dictionary known(hash_input);
    need(known.hex()=="15e2b0d3c33891ebb0f1ef609ec419420c20e320ce94c65fbc8c3312448eb225");
    reject([&]{Dictionary too_small(Bytes(7));});reject([&]{Dictionary too_large(Bytes(max_dictionary+1));});
    std::mt19937 rng(1415);Bytes training(max_dictionary);for(auto&b:training)b=uint8_t(rng());
    Dictionary dict(training),slow(training,true);Workspace tx,rx;
    std::vector<std::pair<Bytes,Bytes>> saved;
    for(size_t n:{size_t(1),size_t(63),size_t(64),size_t(65),size_t(255),size_t(1024),size_t(65535),size_t(65536),size_t(100000)})for(unsigned kind=0;kind<3;++kind){
        Bytes raw(n);for(size_t i=0;i<n;++i)raw[i]=kind==0?0:kind==1?training[i%training.size()]:uint8_t(rng());
        for(const Dictionary* d:{&dict,&slow}){
            auto selected=encode(raw,*d,tx);if(selected.compressed){auto out=decode(selected.bytes,*d,rx);need(std::equal(raw.begin(),raw.end(),out.begin(),out.end()));saved.emplace_back(Bytes(selected.bytes.begin(),selected.bytes.end()),raw);}
            else need(selected.bytes.data()==raw.data()&&selected.bytes.size()==raw.size());++trips;
        }
    }
    // Decode old independent frames out of order after all encoding has finished.
    std::shuffle(saved.begin(),saved.end(),rng);for(auto&[wire,raw]:saved){auto out=decode(wire,dict,rx);need(std::equal(raw.begin(),raw.end(),out.begin(),out.end()));++trips;}
    Bytes raw(8192);std::copy_n(training.begin()+100,raw.size(),raw.begin());auto selected=encode(raw,dict,tx);need(selected.compressed);
    Bytes original(selected.bytes.begin(),selected.bytes.end());
    for(size_t cut=0;cut<original.size();++cut){reject([&]{decode(View(original).first(cut),dict,rx);});need(rx.decoded.empty());}
    auto bad=original;bad.push_back(0);reject([&]{decode(bad,dict,rx);});need(rx.decoded.empty());
    for(size_t at:{size_t(4),size_t(5),size_t(6),size_t(7),size_t(8),size_t(12),size_t(16),size_t(24),size_t(56)}){
        bad=original;bad[at]^=0x80;repair(bad);reject([&]{decode(bad,dict,rx);});need(rx.decoded.empty());
    }
    auto wrong_bytes=training;wrong_bytes[0]^=1;Dictionary wrong(wrong_bytes);
    reject([&]{decode(original,wrong,rx);});need(rx.decoded.empty());
    for(unsigned i=0;i<10000;++i){bad=original;bad[rng()%bad.size()]^=uint8_t(1u<<(rng()%8));if(i%2==0)repair(bad);
        bool ok=true;View out;try{out=decode(bad,dict,rx);}catch(const std::runtime_error&){ok=false;need(rx.decoded.empty());}
        if(ok)need(std::equal(raw.begin(),raw.end(),out.begin(),out.end()));else ++rejections;++changes;
    }
    Bytes maximum(max_raw,7);auto max_selected=encode(maximum,dict,tx);need(max_selected.compressed);auto max_out=decode(max_selected.bytes,dict,rx);need(std::equal(maximum.begin(),maximum.end(),max_out.begin(),max_out.end()));++trips;
    maximum.push_back(0);reject([&]{encode(maximum,dict,tx);});
    reject([&]{encode(dict.bytes(),dict,tx);});reject([&]{encode(tx.wire,dict,tx);});reject([&]{decode(rx.decoded,dict,rx);});
    std::cout<<"{\"pass\":true,\"roundtrips\":"<<trips<<",\"rejections\":"<<rejections<<",\"mutations\":"<<changes<<",\"dictionary_sha256_crosscheck\":true,\"independent_out_of_order_frames\":true,\"maximum_raw_bytes\":"<<max_raw<<"}\n";
}catch(const std::exception&e){std::cerr<<e.what()<<'\n';return 1;}}
