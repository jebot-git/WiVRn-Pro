// SPDX-License-Identifier: MIT
#include "novelty_oracle.h"
#include "cardinality_oracle.h"
#include <cassert>
#include <functional>
#include <iostream>
#include <random>
using namespace novelty_oracle;

std::vector<int32_t> brute(const std::vector<Frame>& f, const std::vector<Deadline>& d, size_t budget, int64_t threshold) {
    std::vector<int32_t> best(budget+1,-1);
    std::function<void(size_t,int,size_t,int32_t)> visit = [&](size_t stage,int previous,size_t skips,int32_t good) {
        if (stage==d.size()) { best[skips]=std::max(best[skips],good);return; }
        if (skips<budget) visit(stage+1,previous,skips+1,good);
        for (size_t rank=size_t(previous+1);rank<f.size();++rank)
            if (f[rank].ready<=d[stage].selection && (previous<0 || (f[size_t(previous)].target<=f[rank].target && f[size_t(previous)].generation<f[rank].generation)))
                visit(stage+1,int(rank),skips,good+int32_t(d[stage].submit-f[rank].receipt<threshold));
    };
    visit(0,-1,0,0);return best;
}
template<class F> void rejected(F callback) {
    bool failed=false;try {callback();}catch(const std::invalid_argument&) {failed=true;}assert(failed);
}
int main() {
    std::mt19937 rng(20261009);
    for (unsigned c=0;c<4096;++c) {
        size_t n=1+rng()%8,d=1+rng()%6,b=std::min(size_t(rng()%4),d-1);
        std::vector<Frame> frames;int64_t target=1,generation=0;
        for (size_t i=0;i<n;++i) {
            target+=rng()%3;generation+=rng()%2;int64_t receive=1+rng()%60;
            frames.push_back({int64_t(i*3+1),target,receive,receive+int64_t(rng()%8),generation});
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
        // A unique source set for every encoded ID must exactly reproduce the
        // independent existing encoded-only implementation, including ties.
        std::vector<cardinality_oracle::Frame> old_frames;
        std::vector<cardinality_oracle::Deadline> old_deadlines;
        for(size_t i=0;i<n;++i) {
            frames[i].generation=int64_t(i);
            old_frames.push_back({frames[i].id,frames[i].target,frames[i].receipt,frames[i].ready});
        }
        for(const auto& deadline:deadlines) old_deadlines.push_back({deadline.sequence,deadline.selection,deadline.submit});
        const auto baseline=cardinality_oracle::solve(old_frames,old_deadlines,b,15);
        const auto unique=solve(frames,deadlines,b,15);
        for(size_t i=0;i<=b;++i) {
            assert(unique.results[i].maximum_good==baseline.results[i].maximum_good);
            assert(unique.results[i].path.size()==baseline.results[i].path.size());
            for(size_t j=0;j<unique.results[i].path.size();++j) {
                assert(unique.results[i].path[j].deadline==baseline.results[i].path[j].deadline);
                assert(unique.results[i].path[j].frame==baseline.results[i].path[j].frame);
            }
        }
    }
    std::vector<Frame> frames;std::vector<Deadline> deadlines;
    for(int64_t i=0;i<20;++i) {
        frames.push_back({i+1,100+i,i*10+1,i*10+1,i});
        deadlines.push_back({i+1,i*10+19,i*10+20});
    }
    auto result=solve(frames,deadlines,1,15);
    assert(result.results[0].maximum_good==0 && result.results[1].maximum_good==19);
    frames={{1,1,15,15,0},{2,2,25,25,1}};deadlines={{1,10,11},{2,20,21},{3,30,31}};
    result=solve(frames,deadlines,1,15);
    assert(!result.results[0].feasible && result.results[1].maximum_good==2);
    assert(result.results[1].path[0].deadline==1 && result.results[1].path[1].deadline==2);
    frames={{1,1,1,1,0},{2,2,1,1,1}};deadlines={{1,10,15},{2,20,25}};
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
    rejected([&]{auto f=frames;f[0].generation=2;solve(f,deadlines,0,15);});
    rejected([&]{auto f=frames;f[0].generation=-1;solve(f,deadlines,0,15);});
    auto repeated=frames;repeated[1].generation=0;
    assert(!solve(repeated,deadlines,0,15).results[0].feasible);
    assert(solve(repeated,deadlines,1,100).results[1].maximum_good==1);
    // Two alternatives in one group must preserve readiness/receipt tradeoffs.
    frames={{1,1,1,1,0},{2,2,19,19,0},{3,3,11,11,1}};
    deadlines={{1,10,11},{2,20,21}};
    auto alternatives=solve(frames,deadlines,0,15);
    assert(alternatives.results[0].maximum_good==2);
    assert(alternatives.results[0].path[0].frame==0);
    frames.clear();deadlines.clear();
    for(int64_t i=0;i<10000;++i) frames.push_back({i,1,1,1,i});
    for(int64_t i=0;i<5000;++i) deadlines.push_back({i+1,i+1,i+2});
    rejected([&]{solve(frames,deadlines,64,15);});
    std::cout << "4096 exhaustive source-novelty skip/cardinality populations and deterministic boundary checks passed\n";
}
