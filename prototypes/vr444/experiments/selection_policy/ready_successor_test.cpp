// SPDX-License-Identifier: MIT
#include "ready_successor_policy.h"
#include <cassert>
#include <iostream>
#include <random>
#include <sstream>
#include <tuple>

namespace rs = wivrn::codec_test::ready_successor_policy;
namespace hp = wivrn::codec_test::horizon_policy;
namespace sp = wivrn::codec_test::selection_policy;
namespace st = wivrn::codec_test::selection_trace;
static st::Frame frame(uint64_t id, int64_t target, bool alpha=false) { return {true,id,id+10,target,10,20,alpha}; }
static void pair(sp::History& h, int slot, st::Frame f) { h[slot]=h[3+slot]=f; if(f.alpha)h[6+slot]=f; }
static bool equal(const st::Frame& a, const st::Frame& b) {
    return std::tie(a.present,a.frame,a.warp,a.view_display_time,a.receive,a.ready,a.alpha) ==
           std::tie(b.present,b.frame,b.warp,b.view_display_time,b.receive,b.ready,b.alpha);
}
static bool equal(const sp::Pair& a, const sp::Pair& b) { for(size_t i=0;i<3;++i)if(!equal(a[i],b[i]))return false; return true; }
static bool equal(const sp::Decision& a, const sp::Decision& b) {
    return a.alpha_required==b.alpha_required && a.fallback==b.fallback && a.max_ahead==b.max_ahead &&
           a.reason==b.reason && a.chosen_slots==b.chosen_slots && a.closest_slots==b.closest_slots &&
           a.eligible_left_slots==b.eligible_left_slots && equal(a.selected,b.selected) && equal(a.closest,b.closest);
}
static std::string json(const sp::History& h, const sp::Pair& previous, const sp::Decision& d, int64_t prior) {
    st::Record r;
    r.policy=st::Policy::bounded_newest; r.history=h; r.previous=previous; r.closest=d.closest; r.selected=d.selected;
    r.predicted_display=1000; r.predicted_period=100; r.previous_predicted_display=prior; r.max_ahead=d.max_ahead;
    r.chosen_slots=d.chosen_slots; r.eligible_left_slots=d.eligible_left_slots;
    r.alpha_required=d.alpha_required; r.selected_alpha_required=d.selected[0].present && d.selected[0].alpha; r.reason=d.reason;
    std::ostringstream out;st::write_json(out,r);auto text=out.str();text.pop_back();return text;
}
int main(int argc, char**) {
    const rs::Config on{true,true,1,1};
    sp::History h{};pair(h,0,frame(1,1000));pair(h,1,frame(2,1100));pair(h,2,frame(3,1200));
    auto result=rs::choose(h,{},1000,100,900,on);
    assert(result.decision.selected[0].frame==2 && result.successor_left_slots[1]==2);
    for(int failure=0;failure<5;++failure) {
        auto bad=h;
        if(failure==0)pair(bad,2,frame(3,1201));
        if(failure==1)bad[5].warp++;
        if(failure==2)bad[5]={};
        if(failure==3)pair(bad,2,frame(3,1100));
        if(failure==4)pair(bad,2,frame(3,1200,true));
        assert(rs::choose(bad,{},1000,100,900,on).decision.selected[0].frame==1);
    }
    auto alpha=h;for(int i=0;i<3;++i)pair(alpha,i,frame(i+1,1000+i*100,true));alpha[8]={};
    assert(rs::choose(alpha,{},1000,100,900,on).decision.selected[0].frame==1);
    const sp::Pair prior_pair{frame(4,1300),frame(4,1300),{}};
    assert(equal(rs::choose(h,prior_pair,1000,100,900,on).decision.selected,prior_pair));
    assert(!rs::choose(h,{},1000,100,900,{true,true,2,1}).valid_input);
    const auto high=std::numeric_limits<int64_t>::max();
    sp::History edge{};pair(edge,0,frame(1,high-20));pair(edge,1,frame(2,high-10));pair(edge,2,frame(3,high));
    assert(rs::choose(edge,{},high-20,10,high-30,on).decision.selected[0].frame==2);

    std::mt19937_64 random(0x5acce550);
    uint64_t advances=0;
    for(int i=0;i<20000;++i) {
        sp::History history{};sp::Pair previous{};
        for(int slot=0;slot<3;++slot) {
            if(random()%5==0)continue;
            pair(history,slot,frame(random()%30,800+random()%500,random()%4==0));
            if(random()%9==0)history[3+slot].warp++;
        }
        if(random()%4==0)previous={frame(random()%30,900),{}, {}};
        if(random()%2==0)previous[1]=previous[0];
        if(i<10000) {
            history={};
            const uint64_t base=1+random()%10000;
            const int64_t phase=static_cast<int64_t>(random()%20)-10;
            const bool alpha_required=random()%3==0;
            for(int slot=0;slot<3;++slot)pair(history,slot,frame(base+slot,1000+slot*100+phase,alpha_required));
            previous={frame(base-1,900),frame(base-1,900),{}};
            if(i%7==0)history[5]={};
            if(i%13==0)history[5].warp++;
        }
        const int64_t prior=i%11==0?0:900;
        const auto base=hp::choose(history,previous,1000,100,prior,{true,1,1}).decision;
        const auto legacy=hp::choose(history,previous,1000,100,prior).decision;
        assert(equal(rs::choose(history,previous,1000,100,prior).decision,legacy));
        assert(equal(rs::choose(history,previous,1000,100,prior,{true,false,1,1}).decision,base));
        const auto chosen=rs::choose(history,previous,1000,100,prior,on);
        const auto& d=chosen.decision;
        if(!d.fallback) {
            assert(sp::coherent(d.selected));
            assert(sp::not_regressing(d.selected[0],d.closest[0]));
            if(sp::coherent(previous))assert(sp::not_regressing(d.selected[0],previous[0]));
            assert(d.selected[0].view_display_time<=1000+d.max_ahead);
            if(d.selected[0].frame>d.closest[0].frame) {
                ++advances;
                const auto successor=chosen.successor_left_slots[d.chosen_slots[0]];
                assert(successor>=0);
                const auto& next=history[successor];
                assert(next.frame>d.selected[0].frame && next.view_display_time>d.selected[0].view_display_time);
                assert(next.view_display_time<=1000+2*d.max_ahead);
                assert(sp::pair_slots(history,successor,d.alpha_required,true)[0]>=0);
            }
        } else assert(sp::coherent(previous)?equal(d.selected,previous):equal(d.selected,sp::Pair{}));
        if(argc>1 && i<256) {
            std::cout<<"{\"synthetic\":true,\"input\":"<<json(history,previous,legacy,prior)
                     <<",\"expected\":"<<json(history,previous,d,prior)<<",\"successor_left_slots\":[";
            for(size_t j=0;j<3;++j){if(j)std::cout<<',';std::cout<<chosen.successor_left_slots[j];}
            std::cout<<"]}\n";
        }
    }
    if(argc==1)std::cout<<"Ready-successor PASS: 20000 disabled/filter-off equivalence histories; "<<advances
                        <<" advancing choices with independent complete-successor witnesses; boundary/alpha/overflow checks\n";
}
