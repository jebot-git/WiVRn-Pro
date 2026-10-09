#include "codec_test/stereo_publication.h"
#include <cassert>
#include <iostream>
#include <memory>
#include <mutex>
#include <thread>
#include <vector>

struct Quaternion { float x=0, y=0, z=0, w=1; };
struct Position { float x=0, y=0, z=0; };
struct Pose { Quaternion orientation; Position position; };
struct Fov { float angleLeft=-1, angleRight=1, angleUp=1, angleDown=-1; };
struct Foveation { std::vector<uint16_t> x{1, 2, 1}, y{2, 4, 2}; };
struct View {
    int64_t display_time=1000;
    std::array<Pose, 2> pose;
    std::array<Fov, 2> fov;
    std::array<Foveation, 2> foveation;
    bool alpha=false;
};
struct Feedback { unsigned stream_index=0; uint64_t frame_index=0; int64_t received_first_packet=10, received_from_decoder=0; };
struct Image { Feedback feedback; View view_info; };
using Handle = std::shared_ptr<Image>;
using Pair = std::array<Handle, 2>;
using Histories = std::array<std::array<Handle, 3>, 2>;

Pair pair(uint64_t frame) {
    Pair p{std::make_shared<Image>(), std::make_shared<Image>()};
    for (unsigned eye=0; eye<2; ++eye) { p[eye]->feedback.stream_index=eye; p[eye]->feedback.frame_index=frame; }
    return p;
}

bool publish(Histories& history, Pair& incoming, int64_t ready=100) {
    return wivrn::codec_test::stereo_publication::replace_pair<Handle>(
        {std::span<Handle>(history[0]), std::span<Handle>(history[1])}, incoming, ready);
}

int main() {
    Histories history{};
    auto initial=pair(1), held=initial;
    assert(publish(history, initial));
    assert(!initial[0] && !initial[1]);
    assert(history[0].back()->feedback.received_from_decoder==100);
    assert(history[1].back()->feedback.received_from_decoder==100);
    unsigned rejected=0;
    const auto reject=[&](Pair candidate, int64_t ready=100) {
        const auto old=history; const auto input=candidate;
        const auto left_ready=candidate[0] ? candidate[0]->feedback.received_from_decoder : 0;
        const auto right_ready=candidate[1] ? candidate[1]->feedback.received_from_decoder : 0;
        assert(!publish(history, candidate, ready));
        assert(history==old && candidate==input);
        assert(!candidate[0] || candidate[0]->feedback.received_from_decoder==left_ready);
        assert(!candidate[1] || candidate[1]->feedback.received_from_decoder==right_ready);
        ++rejected;
    };
    for(unsigned eye=0; eye<2; ++eye) {
        auto p=pair(2); p[eye].reset(); reject(p);
        p=pair(2); p[eye]->feedback.stream_index=2; reject(p);
        p=pair(2); p[eye]->feedback.received_first_packet=0; reject(p);
        p=pair(2); p[eye]->feedback.received_first_packet=101; reject(p);
        for(unsigned member=0; member<11; ++member) {
            p=pair(2); auto& v=p[1]->view_info; auto& q=v.pose[eye]; auto& f=v.fov[eye];
            float* fields[]{&q.orientation.x,&q.orientation.y,&q.orientation.z,&q.orientation.w,
                &q.position.x,&q.position.y,&q.position.z,&f.angleLeft,&f.angleRight,&f.angleUp,&f.angleDown};
            *fields[member] += .125f; reject(p);
        }
        p=pair(2); p[1]->view_info.foveation[eye].x.push_back(9); reject(p);
        p=pair(2); p[1]->view_info.foveation[eye].y[0]++; reject(p);
    }
    auto p=pair(2); p[1]->feedback.frame_index=3; reject(p);
    p=pair(2); p[1]->view_info.display_time++; reject(p);
    p=pair(2); p[1]->view_info.alpha=true; reject(p);
    p=pair(2); p[1]->view_info.pose[0].position.x=-0.0f; reject(p);
    reject(pair(1)); reject(pair(0)); reject(pair(2),0); reject(pair(2),9);
    // An old image lease remains valid after FIFO eviction and incoming carries
    // exactly the evicted objects for feedback outside the shared frames lock.
    for(uint64_t frame=2; frame<=4; ++frame) {
        p=pair(frame); assert(publish(history,p));
        if(frame==4) assert(p==held);
    }
    assert(held[0]->feedback.frame_index==1 && held[1]->feedback.frame_index==1);
    assert(held[0]->feedback.received_from_decoder==100);
    // Exercise the exact transaction helper under its native common-lock
    // contract while a selector observes both histories; TSan checks the lease
    // and history access. This is not a claim about Vulkan completion.
    std::mutex mutex;
    bool finished=false;
    std::thread reader([&] {
        for(;;) {
            std::lock_guard lock(mutex);
            assert(history[0].back()->feedback.frame_index==history[1].back()->feedback.frame_index);
            assert(history[0].back()->feedback.received_from_decoder==history[1].back()->feedback.received_from_decoder);
            if(finished) break;
        }
    });
    for(uint64_t frame=5; frame<5005; ++frame) {
        p=pair(frame);
        std::lock_guard lock(mutex);
        assert(publish(history,p,static_cast<int64_t>(frame)+1000));
    }
    { std::lock_guard lock(mutex); finished=true; }
    reader.join();
    std::cout << "PASS atomic stereo publication: " << rejected << " rejection cases, 5000 synchronized pair updates, retained eviction leases\n";
}
