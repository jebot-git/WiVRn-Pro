// SPDX-License-Identifier: MIT
#include "codec_pacer_tail.h"
#include <cassert>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <random>
#include <string>
#include <unistd.h>
using namespace wivrn::codec_test::pacer_tail;
struct Sample { int64_t frame_id=-1,present=0,decoded=0; };
static std::size_t checks=0;
#define CHECK(x) do { ++checks; if (!(x)) { std::cerr<<__LINE__<<": " #x "\n";std::abort(); } } while(0)
int main() {
    CHECK(checked_margin_ns(1.f)==1'000'000);CHECK(checked_margin_ns(-1.f)==-1);
    CHECK(checked_margin_bits(0x7f800000u)==-1);
    CHECK(checked_margin_bits(0x7fc00000u)==-1);
    CHECK(parse_window(nullptr)==0); CHECK(parse_window("")==0);CHECK(parse_window("0")==0);
    CHECK(parse_window("360")==360);CHECK(parse_window("1800")==1800);
    for (auto s:{"1","359","1801","-1","+900","900 "," 900","900x","true","4294967296"}) {
        bool threw=false;try { (void)parse_window(s); } catch(const std::runtime_error&) { threw=true; } CHECK(threw);
    }
    std::vector<Sample> bank;
    for(int64_t f=0;f<2000;++f) bank.push_back({f,1'000'000'000,1'000'000'000+(f<500?40'000'000:8'000'000)});
    auto d=choose(bank,900,1999,1'000'000,41'000'000);
    CHECK(d.valid&&d.recent_used&&d.slew_limited);CHECK(d.legacy_tail==40'000'000&&d.recent_tail==8'000'000);
    CHECK(d.applied==40'000'000&&d.recent_count==900&&d.oldest_recent==1100);
    // Repeated support decreases gradually, never below selected tail+margin.
    for(int i=0;i<40;++i) { auto next=choose(bank,900,1999,1'000'000,d.applied);CHECK(next.applied>=9'000'000);CHECK(next.applied>=d.applied-1'000'000);d=next; }
    CHECK(d.applied==9'000'000&&!d.slew_limited);
    // A new high latency recent tail raises the budget immediately.
    for(int i=1900;i<2000;++i) bank[i].decoded=1'060'000'000;
    d=choose(bank,900,1999,1'000'000,9'000'000);CHECK(d.applied==61'000'000&&!d.slew_limited);
    // Inclusive 900-frame boundary, old late arrival cannot refresh its age.
    bank[0].decoded=1'900'000'000;d=choose(bank,900,1999,1'000'000,61'000'000);CHECK(d.newest==1999&&d.oldest_recent==1100&&d.recent_count==900);
    std::vector<Sample> sparse;for(int64_t i=0;i<127;++i)sparse.push_back({1900+i,1,9'000'001});sparse.push_back({0,1,80'000'001});
    d=choose(sparse,900,2026,1'000'000,9'000'000);CHECK(d.valid&&!d.recent_used&&d.applied==81'000'000);
    sparse.push_back({2027,1,9'000'001});d=choose(sparse,900,2027,1'000'000,81'000'000);CHECK(d.recent_used&&d.applied==80'000'000);
    CHECK(!choose(sparse,900,2027,0,0,2028).valid);
    CHECK(choose(sparse,900,2027,0,0,2027).valid);
    auto duplicate=choose(sparse,900,2027,0,100'000'000,2027);CHECK(duplicate.valid&&duplicate.decrease_blocked&&duplicate.applied==100'000'000);
    auto bad=sparse;bad.back().frame_id=2028;CHECK(!choose(bad,900,2027,0,0).valid);
    bad=sparse;bad.back().frame_id=0;CHECK(!choose(bad,900,2027,0,0).valid);
    bad=sparse;bad.back().present=-1;CHECK(!choose(bad,900,2027,0,0).valid);
    CHECK(!choose(sparse,900,2027,-1,0).valid);CHECK(!choose(sparse,900,2027,20'000'001,0).valid);
    CHECK(!choose(sparse,359,2027,0,0).valid);CHECK(!choose(sparse,900,2027,0,-1).valid);
    CHECK(!choose(std::vector<Sample>{},900,2027,0,0).valid);CHECK(!choose(std::vector<Sample>(5001),900,2027,0,0).valid);
    CHECK(!choose(std::vector<Sample>{{0,1,std::numeric_limits<int64_t>::max()}},900,0,10,0).valid);
    // Independent sorted reference covers quantile index, gap/late order,
    // support fallback and previous-budget slew across randomized populations.
    std::mt19937 rng(20261009);
    for(int trial=0;trial<2000;++trial) {
        std::vector<Sample> v;std::vector<int64_t> all,recent;int64_t frame=0;
        int count=1+rng()%5000;uint32_t window=360+rng()%1441;int64_t margin=rng()%20'000'001,previous=rng()%100'000'001;
        for(int i=0;i<count;++i) { frame+=1+rng()%3;int64_t delay=1+rng()%90'000'000;v.push_back({frame,10'000'000'000,10'000'000'000+delay});all.push_back(delay); }
        for(const auto & x:v)if(x.frame_id>=frame-int64_t(window)+1)recent.push_back(x.decoded-x.present);
        std::sort(all.begin(),all.end());std::sort(recent.begin(),recent.end());std::shuffle(v.begin(),v.end(),rng);
        auto q=choose(v,window,frame,margin,previous);bool used=recent.size()>=128;
        int64_t want=(used?recent[recent.size()*995/1000]:all[all.size()*995/1000])+margin;
        if(used&&previous>want+1'000'000)want=previous-1'000'000;
        CHECK(q.valid&&q.recent_used==used&&q.applied==want);
        auto legacy=choose(v,0,frame,margin,previous);CHECK(legacy.valid&&!legacy.recent_used&&legacy.applied==all[all.size()*995/1000]+margin);
    }
    auto path=(std::filesystem::temp_directory_path()/ ("pacer-tail-"+std::to_string(getpid())+".jsonl")).string();
    { Trace off(nullptr,nullptr,false);CHECK(off.window==0); }
    for(auto option:{0,1,2}) { bool threw=false;try {Trace invalid("900",option==0?nullptr:option==1?"relative":path.c_str(),false);}catch(const std::runtime_error&){threw=true;}CHECK(threw); }
    { Trace trace("900",path.c_str(),true);for(int i=0;i<4100;++i)trace.append({1,0,uint64_t(i+1),false,d}); }
    std::ifstream file(path);std::string text((std::istreambuf_iterator<char>(file)),{});
    CHECK(text.find("\"records\":4096,\"dropped\":4,\"complete\":true")!=std::string::npos);
    bool threw=false;try{Trace exists("900",path.c_str(),true);}catch(const std::runtime_error&){threw=true;}CHECK(threw);
    std::filesystem::remove(path);
    CHECK(!parse_rolling(nullptr));CHECK(!parse_rolling(""));CHECK(!parse_rolling("alternating"));CHECK(parse_rolling("rolling"));
    for (auto mode:{"Rolling","rolling ","true","0","alternating ","unknown"}) {
        bool rejected=false;try{(void)parse_rolling(mode);}catch(const std::runtime_error&){rejected=true;}CHECK(rejected);
    }
    {bool rejected=false;try{Trace invalid("0",nullptr,false,"rolling");}catch(const std::runtime_error&){rejected=true;}CHECK(rejected);}
    {Trace rolling("900",path.c_str(),true,"rolling");CHECK(rolling.rolling);
     rolling.produce_snapshot(7);rolling.consume_snapshot(2,true);rolling.consume_snapshot(5,false);
     auto one=choose(std::vector<Sample>{{0,1,101}},900,0,0,0);
     rolling.append({1,0,5,false,one,10,20,30});}
    std::ifstream rolling_file(path);std::string rolling_text((std::istreambuf_iterator<char>(rolling_file)),{});
    CHECK(rolling_text.find("\"schema\":2,\"feedback_bank\":\"rolling\"")!=std::string::npos);
    CHECK(rolling_text.find("\"consumed_snapshots\":2,\"coalesced_snapshots\":3,\"empty_snapshots\":1")!=std::string::npos);
    CHECK(rolling_text.find("\"last_consumed_batch\":5,\"unconsumed_snapshots\":2")!=std::string::npos);
    std::filesystem::remove(path);
    std::cout<<"pacer recent tail checks="<<checks<<" randomized=2000\n";
}
