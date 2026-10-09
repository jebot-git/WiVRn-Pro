// Actual pacer implementation with only external runtime/time/variable APIs stubbed.
#include <bits/stdc++.h>
#define private public
#include "pacer.h"
#undef private
#include <unistd.h>
static void require(bool x) { if(!x)std::abort(); }
static void until(const std::function<bool()>& done) {
    for(int i=0;i<2000;++i){if(done())return;std::this_thread::sleep_for(std::chrono::milliseconds(1));}
    std::abort();
}
int main() {
    std::string base="/tmp/sub15-native-worker-"+std::to_string(getpid());
    setenv("WIVRN_CODEC_PACER_TRACE",(base+"-predict.jsonl").c_str(),1);
    setenv("WIVRN_CODEC_TEST_PACER_TAIL_TRACE",(base+"-tail.jsonl").c_str(),1);
    setenv("WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES","900",1);
    {
        wivrn::pacer p(11'111'111);
        auto budget=[&]{std::lock_guard lock(p.mutex);return p.safe_present_to_decoded_ns;};
        auto bank=[&](int count,int64_t start,int64_t delay){
            std::lock_guard lock(p.mutex);std::lock_guard compute(p.compute_mutex);
            std::ranges::fill(p.frame_times_compute,wivrn::pacer::frame_time{});
            for(int i=0;i<count;++i)p.frame_times_compute[i]={start+i,1'000'000'000,1'000'000'000+delay};
            p.codec_test_tail_boundary=start+count-1;p.codec_test_tail_compute_epoch=p.codec_test_tail_epoch;
            ++p.codec_test_tail_batch;p.compute_cv.notify_all();
        };
        bank(127,0,40'000'000);until([&]{return budget()==41'000'000;});
        bank(900,1000,8'000'000);until([&]{return budget()==40'000'000;});
        for(int i=0;i<100;++i)p.compute_cv.notify_all();
        std::this_thread::sleep_for(std::chrono::milliseconds(20));require(budget()==40'000'000);
        bank(900,1000,8'000'000);std::this_thread::sleep_for(std::chrono::milliseconds(20));require(budget()==40'000'000);
        bank(900,1100,60'000'000);until([&]{return budget()==61'000'000;});
        bank(900,0,1'000'000);std::this_thread::sleep_for(std::chrono::milliseconds(20));require(budget()==61'000'000);
        p.reset();require(budget()==61'000'000);
        {std::lock_guard lock(p.mutex);require(p.codec_test_tail_newest==-1&&p.codec_test_tail_epoch==1);}
        bank(900,0,8'000'000);until([&]{return budget()==60'000'000;});
        // Destruction while the worker awaits another batch must observe stop.
    }
    std::ifstream f(base+"-tail.jsonl");std::string data((std::istreambuf_iterator<char>(f)),{});
    require(data.find("\"complete\":true")!=std::string::npos);
    require(data.find("\"valid\":false")!=std::string::npos); // backstep refused
    require(data.find("\"dropped\":0")!=std::string::npos);
    std::filesystem::remove(base+"-tail.jsonl");std::filesystem::remove(base+"-predict.jsonl");
    std::cout<<"actual native worker startup/sparse/slew/increase/spurious/backstep/reset/stop checks passed\n";
}
