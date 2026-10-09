// SPDX-License-Identifier: MIT
// Host-only deterministic queue tests; no Vulkan, ADB, or device state.
#include "codec_test/stereo_admission.h"
#include <atomic>
#include <cassert>
#include <chrono>
#include <future>
#include <iostream>
#include <thread>
#include <vector>
using namespace wivrn::codec_test;
using namespace std::chrono_literals;
struct Job { uint64_t frame; unsigned eye; int token=0; };
using Queue=StereoAdmission<Job>;
Header header(uint64_t frame,unsigned eye) { return {Kind::vr444,576,640,eye,frame,frame*17}; }
bool put(Queue& q,uint64_t frame,unsigned eye,int token=0) { return q.submit(header(frame,eye),{frame,eye,token}); }
void pair(Queue& q,uint64_t frame) { assert(put(q,frame,0));assert(put(q,frame,1)); }
void consume(Queue& q,uint64_t frame,bool success=true) {
    auto left=q.wait_take(0),right=q.wait_take(1);
    assert(left&&right&&left->frame==frame&&right->frame==frame&&left->eye==0&&right->eye==1);
    q.finish(0,frame,success);q.finish(1,frame,true);
}
void reordered_and_missing() {
    Queue q;
    assert(put(q,1,1));assert(q.snapshot().admitted_pairs==0);
    assert(put(q,1,0));consume(q,1);
    for(uint64_t f=2;f<=100;++f){assert(put(q,f,0));assert(q.snapshot().pending_frames<=2);}
    assert(q.snapshot().admitted_pairs==1);
    assert(put(q,100,1));consume(q,100);
    assert(!put(q,99,1));assert(q.snapshot().stale_eyes==1);
    assert(q.snapshot().completed_pairs==2);q.stop();
}
void newest_complete_and_slow_eye() {
    Queue q;pair(q,1);
    assert(q.wait_take(0)->frame==1);assert(q.wait_take(1)->frame==1);
    q.finish(0,1,true);
    pair(q,2);pair(q,3);
    assert(put(q,4,0));assert(put(q,5,0));
    // Complete pair 3 survives the stream of newer left-eye orphans.
    assert(q.snapshot().pending_frames==2);
    std::promise<void> waiting;
    auto next=std::async(std::launch::async,[&]{waiting.set_value();return q.wait_take(0);});
    waiting.get_future().wait();assert(next.wait_for(20ms)==std::future_status::timeout);
    q.finish(1,1,true);
    assert(next.wait_for(1s)==std::future_status::ready);assert(next.get()->frame==3);
    assert(q.wait_take(1)->frame==3);q.finish(1,3,true);q.finish(0,3,true);
    assert(put(q,5,1));consume(q,5);
    auto stats=q.snapshot();assert(stats.admitted_pairs==3&&stats.completed_pairs==3&&stats.dropped_eyes==3);q.stop();
}
void duplicate_stale_and_identity() {
    Queue q;
    assert(put(q,10,0,41));assert(!put(q,10,0,99));assert(put(q,10,1));
    assert(q.wait_take(0)->token==41);assert(q.wait_take(1)->frame==10);
    assert(!put(q,10,1));q.finish(0,10,true);q.finish(1,10,true);
    assert(!put(q,9,0));assert(q.snapshot().duplicate_eyes==2);
    for(int field=0;field<4;++field){
        uint64_t f=11+field;assert(put(q,f,0));auto h=header(f,1);
        if(field==0)h.warp++;if(field==1)h.width++;if(field==2)h.height++;if(field==3)h.kind=Kind::raw_rgba;
        assert(!q.submit(h,{f,1}));assert(!put(q,f,1));assert(q.snapshot().pending_frames==0);
    }
    assert(q.snapshot().mismatched_pairs==4);
    pair(q,15);consume(q,15);q.stop();
}
void failure_and_teardown() {
    Queue q;pair(q,1);pair(q,2);consume(q,1,false);consume(q,2);
    assert(q.snapshot().failed_pairs==1&&q.snapshot().completed_pairs==1);
    auto left=std::async(std::launch::async,[&]{return q.wait_take(0);});
    auto right=std::async(std::launch::async,[&]{return q.wait_take(1);});
    assert(put(q,3,0));q.stop();q.stop();
    assert(left.wait_for(1s)==std::future_status::ready&&!left.get());
    assert(right.wait_for(1s)==std::future_status::ready&&!right.get());
    assert(!put(q,3,1));assert(q.snapshot().closed);
    Queue half;pair(half,1);assert(half.wait_take(0));half.stop();
    assert(!half.wait_take(1));half.finish(0,1,false);assert(half.snapshot().abandoned_pairs==1);
    Queue old_generation,new_generation;assert(put(old_generation,1,0));assert(put(new_generation,1,1));
    old_generation.stop();assert(new_generation.snapshot().admitted_pairs==0);
    assert(put(new_generation,1,0));consume(new_generation,1);new_generation.stop();
    bool rejected=false;try{new_generation.wait_take(2);}catch(const std::invalid_argument&){rejected=true;}assert(rejected);
}
void concurrent_overload() {
    Queue q;
    std::array<std::vector<uint64_t>,2> decoded;
    std::array<std::thread,2> workers;
    for(unsigned eye=0;eye<2;++eye)workers[eye]=std::thread([&,eye]{
        while(auto job=q.wait_take(eye)){
            assert(job->eye==eye);decoded[eye].push_back(job->frame);
            if(eye==1)for(int i=0;i<30;++i)std::this_thread::yield();
            q.finish(eye,job->frame,true);
        }
    });
    for(uint64_t frame=1;frame<=2000;++frame){
        unsigned first=unsigned(frame%2);put(q,frame,first);put(q,frame,1-first);
        assert(q.snapshot().pending_frames<=2);
    }
    // Wait for the last (newest complete) pair to finish without timing-based
    // admission assumptions. A timeout makes a deadlock fail the test process.
    auto deadline=std::chrono::steady_clock::now()+5s;
    while(q.snapshot().active || q.snapshot().pending_frames){
        assert(std::chrono::steady_clock::now()<deadline);std::this_thread::yield();
    }
    q.stop();for(auto& worker:workers)worker.join();
    assert(decoded[0]==decoded[1]);assert(!decoded[0].empty()&&decoded[0].back()==2000);
    for(size_t i=1;i<decoded[0].size();++i)assert(decoded[0][i]>decoded[0][i-1]);
    assert(q.snapshot().completed_pairs==decoded[0].size());
}
int main() {
    reordered_and_missing();newest_complete_and_slow_eye();duplicate_stale_and_identity();
    failure_and_teardown();for(int i=0;i<20;++i)concurrent_overload();
    std::cout<<"Stereo admission: deterministic cases and 20 concurrent overload runs passed\n";
}
