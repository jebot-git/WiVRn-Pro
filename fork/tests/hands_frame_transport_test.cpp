// SPDX-License-Identifier: GPL-3.0-or-later
#include "compositor/hands_frame_transport.h"
#include "driver/headset_capture.h"
#include <cassert>
#include <fstream>
#include <sys/file.h>
#include <vector>
using namespace std::chrono_literals;
std::vector<std::byte> read(const std::filesystem::path & path) {
    std::ifstream stream(path,std::ios::binary);
    std::vector<char> chars((std::istreambuf_iterator<char>(stream)),{});
    std::vector<std::byte> result(chars.size());std::memcpy(result.data(),chars.data(),chars.size());return result;
}
int main() {
    char temporary[]="/tmp/wivrn-frame-transport-XXXXXX";assert(mkdtemp(temporary));
    auto root=std::filesystem::path(temporary);auto path=root/"frame";
    std::vector<std::byte> a(1024*1024,std::byte{0x31}),b(a.size(),std::byte{0x72});
    assert(wivrn::publish_hands_frame(path,{},a));
    int held=open(path.c_str(),O_RDONLY|O_CLOEXEC);assert(held>=0);assert(!flock(held,LOCK_SH|LOCK_NB));
    assert(wivrn::publish_hands_frame(path,{},b));assert(read(path)==b);
    std::vector<std::byte> previous(a.size());assert(::read(held,previous.data(),previous.size())==ssize_t(previous.size()));assert(previous==a);close(held);
    std::atomic<bool> done=false;
    std::jthread writer([&] {for(int i=0;i<25;++i)assert(wivrn::publish_hands_frame(path,{},i%2?a:b));done=true;});
    while(!done) {auto bytes=read(path);assert(bytes==a||bytes==b);}
    writer.join();
    auto runtime=root/"wivrn-pro";std::filesystem::create_directory(runtime);chmod(runtime.c_str(),0700);setenv("XDG_RUNTIME_DIR",temporary,1);
    uint64_t now=std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count();
    std::array<std::byte,9> lease{};std::memcpy(lease.data(),&now,8);lease[8]=std::byte{1};
    assert(wivrn::publish_hands_frame(runtime/"hands-frame-headset.lease",{},lease));
    {
        wivrn::headset_capture_bridge bridge;std::optional<wivrn::to_headset::headset_capture_control> control;
        for(int i=0;i<40;++i) {auto next=bridge.control();if(next&&next->enabled) {control=next;break;}std::this_thread::sleep_for(5ms);}
        assert(control&&control->frames&&control->request_id);
        std::vector<std::byte> jpeg{std::byte{0xff},std::byte{0xd8},std::byte{0xff},std::byte{0xd9}};
        bridge.submit({.request_id=control->request_id+1,.age_ms=0,.status="forged",.jpeg=jpeg});
        std::this_thread::sleep_for(80ms);assert(!std::filesystem::exists(runtime/"hands-frame-headset.jpg"));
        bridge.submit({.request_id=control->request_id,.age_ms=0,.status="ready",.jpeg=jpeg});
        for(int i=0;i<40&&!std::filesystem::exists(runtime/"hands-frame-headset.jpg");++i)std::this_thread::sleep_for(5ms);
        assert(read(runtime/"hands-frame-headset.jpg").size()==44);
        std::filesystem::remove(runtime/"hands-frame-headset.lease");
        std::this_thread::sleep_for(80ms);
        auto disabled=bridge.control();assert(disabled&&!disabled->enabled);
        bridge.submit({.request_id=control->request_id,.age_ms=0,.status="late",.jpeg=jpeg});
        std::this_thread::sleep_for(80ms);assert(!std::filesystem::exists(runtime/"hands-frame-headset.jpg"));
    }
    std::filesystem::remove_all(root);
}
