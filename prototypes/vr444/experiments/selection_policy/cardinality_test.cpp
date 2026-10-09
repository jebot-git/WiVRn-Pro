// SPDX-License-Identifier: MIT
#include "cardinality_oracle.h"
#include <cassert>
#include <functional>
#include <iostream>
#include <random>
using namespace cardinality_oracle;

std::vector<int32_t> brute(const std::vector<Frame>& f, const std::vector<Deadline>& d, size_t budget, int64_t threshold) {
    std::vector<int32_t> best(budget+1,-1);
    std::function<void(size_t,int,size_t,int32_t)> visit = [&](size_t stage,int previous,size_t skips,int32_t good) {
        if (stage==d.size()) { best[skips]=std::max(best[skips],good);return; }
        if (skips<budget) visit(stage+1,previous,skips+1,good);
        for (size_t rank=size_t(previous+1);rank<f.size();++rank)
            if (f[rank].ready<=d[stage].selection && (previous<0 || f[size_t(previous)].target<=f[rank].target))
                visit(stage+1,int(rank),skips,good+int32_t(d[stage].submit-f[rank].receipt<threshold));
    };
    visit(0,-1,0,0);return best;
}
template<class F> void rejected(F callback) {
    bool failed=false;try {callback();}catch(const std::invalid_argument&) {failed=true;}assert(failed);
}
int main() {
    std::mt19937 rng(20261009);
    for (unsigned c=0;c<1024;++c) {
        size_t n=1+rng()%8,d=1+rng()%6,b=std::min(size_t(rng()%4),d-1);
        std::vector<Frame> frames;int64_t target=1;
        for (size_t i=0;i<n;++i) {
            target+=rng()%3;int64_t receive=1+rng()%60;
            frames.push_back({int64_t(i*3+1),target,receive,receive+int64_t(rng()%8)});
        }
        std::vector<Deadline> deadlines;
        for(size_t i=0;i<d;++i) deadlines.push_back({int64_t(i*2+1),int64_t(12+i*10),int64_t(15+i*10)});
        auto expected=brute(frames,deadlines,b,15);
        auto actual=solve(frames,deadlines,b,15);
        assert(actual.results.size()==b+1);
        for(size_t i=0;i<=b;++i) {
            assert(actual.results[i].maximum_good==expected[i]);
            assert(actual.results[i].feasible==(expected[i]>=0));
            if(expected[i]>=0) assert(actual.results[i].path.size()==d-i);
        }
    }
    std::vector<Frame> frames;std::vector<Deadline> deadlines;
    for(int64_t i=0;i<20;++i) {
        frames.push_back({i+1,100+i,i*10+1,i*10+1});
        deadlines.push_back({i+1,i*10+19,i*10+20});
    }
    auto result=solve(frames,deadlines,1,15);
    assert(result.results[0].maximum_good==0 && result.results[1].maximum_good==19);
    frames={{1,1,15,15},{2,2,25,25}};deadlines={{1,10,11},{2,20,21},{3,30,31}};
    result=solve(frames,deadlines,1,15);
    assert(!result.results[0].feasible && result.results[1].maximum_good==2);
    assert(result.results[1].path[0].deadline==1 && result.results[1].path[1].deadline==2);
    frames={{1,1,1,1},{2,2,1,1}};deadlines={{1,10,15},{2,20,25}};
    result=solve(frames,deadlines,1,14);
    assert(result.results[0].maximum_good==0); // age14 boundary is not good
    rejected([&]{solve(frames,deadlines,2,15);});
    rejected([&]{solve(frames,deadlines,0,0);});
    rejected([&]{auto f=frames;f[1].target=0;solve(f,deadlines,0,15);});
    rejected([&]{auto f=frames;f[0].target=3;solve(f,deadlines,0,15);});
    rejected([&]{auto f=frames;f[1].id=1;solve(f,deadlines,0,15);});
    rejected([&]{auto f=frames;f[0].ready=0;solve(f,deadlines,0,15);});
    rejected([&]{auto d=deadlines;d[1].selection=10;solve(frames,d,0,15);});
    rejected([&]{auto d=deadlines;d[1].submit=1;solve(frames,d,0,15);});
    frames.clear();deadlines.clear();
    for(int64_t i=0;i<10000;++i) frames.push_back({i,1,1,1});
    for(int64_t i=0;i<5000;++i) deadlines.push_back({i+1,i+1,i+2});
    rejected([&]{solve(frames,deadlines,64,15);});
    std::cout << "1024 exhaustive skip/cardinality populations and deterministic boundary checks passed\n";
}
