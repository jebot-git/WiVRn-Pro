// SPDX-License-Identifier: MIT
#define WIVRN_CODEC_TESTS 1
#include "server/encoder/encode_batch.h"
#include <cassert>
#include <memory>
#include <vector>
struct Fake {
 unsigned stream_idx;size_t bytes;bool capped=true,fail=false,kept=false;unsigned rejected=0;
 size_t staged_output_bytes()const{return bytes;}
 bool test_pair_capped()const{return capped;}
 void test_pair_rejected(size_t,const char*){++rejected;}
 void commit_encoded(bool keep){kept=keep;}
};
int main(){
 std::vector<std::unique_ptr<Fake>> enc;
 enc.emplace_back(new Fake{0,48+1});enc.emplace_back(new Fake{1,262144-49});
 unsigned failures=0;std::array submitted{true,true,false};
 auto run=[&]{wivrn::encode_batch(enc,submitted,[](Fake&f){if(f.fail)throw std::runtime_error("peer failed");},[&](Fake&,const std::exception&){++failures;});};
 run();assert(enc[0]->kept&&enc[1]->kept); // exact cap, strongly asymmetric
 enc.emplace_back(new Fake{2,0,false});run();assert(enc[0]->kept&&enc[1]->kept); // actual native three-slot construction
 submitted[2]=true;run();assert(!enc[0]->kept&&!enc[1]->kept);submitted[2]=false;
 enc[2]->bytes=99;run();assert(!enc[0]->kept&&!enc[1]->kept);enc[2]->bytes=0;
 enc[0]->rejected=enc[1]->rejected=0;
 ++enc[1]->bytes;run();assert(!enc[0]->kept&&!enc[1]->kept&&enc[0]->rejected==1);
 enc[1]->bytes=100;enc[1]->fail=true;run();assert(!enc[0]->kept&&!enc[1]->kept&&failures==1);
 enc[1]->fail=false;enc[1]->bytes=0;run();assert(!enc[0]->kept&&!enc[1]->kept);
 enc[1]->bytes=100;enc[1]->capped=false;run();assert(!enc[0]->kept&&!enc[1]->kept);
 enc[1]->capped=true;enc[1]->stream_idx=0;run();assert(!enc[0]->kept&&!enc[1]->kept);
 enc[1]->stream_idx=1;enc[1]->capped=false;
 enc[0]->capped=false;enc[1]->bytes=300000;run();assert(enc[0]->kept&&enc[1]->kept); // legacy untouched
}
