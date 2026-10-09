// Actual native on_feedback + worker, with only external API stubs.
#include <bits/stdc++.h>
#define private public
#include "pacer.h"
#undef private
#include "driver/clock_offset.h"
static size_t checks=0;
#define REQUIRE(x) do {++checks;if(!(x)){std::cerr<<__LINE__<<": " #x "\n";std::abort();}}while(0)
static void until(const std::function<bool()>& f){for(int i=0;i<2000;++i){if(f())return;std::this_thread::sleep_for(std::chrono::milliseconds(1));}std::abort();}
int main(int argc,char**argv){
 REQUIRE(argc==2);const std::string base=argv[1];
 setenv("WIVRN_CODEC_PACER_TRACE",(base+"-predict.jsonl").c_str(),1);
 setenv("WIVRN_CODEC_TEST_PACER_TAIL_TRACE",(base+"-tail.jsonl").c_str(),1);
 setenv("WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES","900",1);
 setenv("WIVRN_CODEC_TEST_PACER_BANK_MODE","rolling",1);
 std::mt19937 rng(20261009);size_t snapshots=0;
 {
  wivrn::pacer p(11'111'111);wivrn::clock_offset clock;
  std::vector<wivrn::pacer::frame_time> reference(5000);
  auto same=[&](const auto&a,const auto&b){return a.frame_id==b.frame_id&&a.present==b.present&&a.decoded==b.decoded;};
  for(int64_t frame=0;frame<=6600;++frame){
   if(frame==3201){p.reset();std::fill(reference.begin(),reference.end(),wivrn::pacer::frame_time{});}
   int64_t id,wake,present,slop,display;p.predict(id,wake,present,slop,display);REQUIRE(id==frame);
   auto send=[&](int eye,int64_t delay,int times=1,int64_t blitted=1){
    wivrn::from_headset::feedback feedback;feedback.frame_index=id;feedback.stream_index=eye;feedback.times_displayed=times;feedback.blitted=blitted?present+30'000'000:0;feedback.received_from_decoder=present+delay;
    if(times<=1&&blitted){auto&r=reference[id%5000];if(r.frame_id!=id)r={id,present,0};r.decoded=std::max(r.decoded,feedback.received_from_decoder);}
    p.on_feedback(feedback,clock);
   };
   // Nontrigger frames include absent first-use feedback. Never fabricate a
   // complete-pair sample for those gaps; the reference has identical admission.
   const bool accepted=id%100==0||rng()%9!=0;
   const int64_t left_delay=20'000'000+rng()%20'000'000;
   if(accepted)send(0,left_delay);
   else{send(0,left_delay,2);send(0,left_delay,1,0);}
   if(id%100==0){
    ++snapshots;
    {
     std::lock_guard lock(p.mutex);std::lock_guard compute(p.compute_mutex);
     REQUIRE(p.codec_test_tail_trace.rolling);REQUIRE(p.frame_times.size()==5000);
     for(size_t i=0;i<5000;++i){REQUIRE(same(p.frame_times[i],reference[i]));REQUIRE(same(p.frame_times_compute[i],reference[i]));}
    }
    until([&]{std::lock_guard lock(p.mutex);return p.codec_test_tail_newest==id;});
    std::vector<int64_t> all,recent;
    for(const auto&r:reference)if(r.decoded>r.present){all.push_back(r.decoded-r.present);if(r.frame_id>=id-899)recent.push_back(r.decoded-r.present);}
    std::sort(all.begin(),all.end());std::sort(recent.begin(),recent.end());
    const auto&use=recent.size()>=128?recent:all;const auto lower=use[use.size()*995/1000]+1'000'000;
    {std::lock_guard lock(p.mutex);REQUIRE(p.safe_present_to_decoded_ns>=lower);}
   }
   // This right-eye completion occurs AFTER a trigger's immutable copy. It must
   // remain in the cumulative ring and enter the next snapshot, not be orphaned
   // in the former compute bank. Some frames deliberately have no right report.
   if(accepted&&rng()%7!=0)send(1,left_delay+1+rng()%5'000'000);
   if(id>8){auto before=reference;wivrn::from_headset::feedback old;old.frame_index=id-8;old.blitted=1;old.received_from_decoder=1'000'000'000'000;p.on_feedback(old,clock);
    std::lock_guard lock(p.mutex);for(size_t i=0;i<5000;++i)REQUIRE(same(p.frame_times[i],before[i]));}
  }
  // Ring wrap and reset exercised; at least900 recent identities can coexist.
  size_t live=0;for(const auto&r:reference)live+=r.frame_id>=5701;REQUIRE(live>750);
 }
 std::ifstream stream(base+"-tail.jsonl");std::string text((std::istreambuf_iterator<char>(stream)),{});
 REQUIRE(text.find("\"feedback_bank\":\"rolling\"")!=std::string::npos);
 REQUIRE(text.find("\"eye_pair_completeness_recorded\":false")!=std::string::npos);
 REQUIRE(text.find("\"complete\":true")!=std::string::npos);
 REQUIRE(text.find("\"dropped\":0")!=std::string::npos);
 std::cout<<"actual rolling feedback checks="<<checks<<" snapshots="<<snapshots<<" seeded_frames=6601\n";
}
