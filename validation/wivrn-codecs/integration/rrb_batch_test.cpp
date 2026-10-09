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
 void test_pair_rejected(size_t){++rejected;}
 void commit_encoded(bool keep){kept=keep;}
};
int main(){
 std::vector<std::unique_ptr<Fake>> enc;
 enc.emplace_back(new Fake{0,48+1});enc.emplace_back(new Fake{1,262144-49});
 unsigned failures=0;
 auto run=[&]{wivrn::encode_batch(enc,std::array{true,true,false},[](Fake&f){if(f.fail)throw std::runtime_error("peer failed");},[&](Fake&,const std::exception&){++failures;});};
 run();assert(enc[0]->kept&&enc[1]->kept); // exact cap, strongly asymmetric
 ++enc[1]->bytes;run();assert(!enc[0]->kept&&!enc[1]->kept&&enc[0]->rejected==1);
 enc[1]->bytes=100;enc[1]->fail=true;run();assert(!enc[0]->kept&&!enc[1]->kept&&failures==1);
 enc[1]->fail=false;enc[1]->bytes=0;run();assert(!enc[0]->kept&&!enc[1]->kept);
 enc[1]->bytes=100;enc[1]->capped=false;run();assert(!enc[0]->kept&&!enc[1]->kept);
 enc[0]->capped=false;enc[1]->bytes=300000;run();assert(enc[0]->kept&&enc[1]->kept); // legacy untouched
}
