// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "wivrn_packets.h"
#include "compositor/hands_frame_transport.h"

#include <atomic>
#include <array>
#include <cstdlib>
#include <bit>
#include <cstring>
#include <mutex>
#include <optional>
#include <thread>
#include <sys/stat.h>
namespace wivrn
{
// The paired, encrypted control connection carries bounded JPEGs. Filesystem
// publication and lease polling run off the network and compositor threads.
class headset_capture_bridge {
    std::filesystem::path root;
    std::atomic<bool> enabled{false}, frames{false};
    std::atomic<uint64_t> request{0};
    uint64_t last_control = 0;
    bool last_enabled = false, last_frames = false;
    std::mutex mutex;
    std::optional<from_headset::headset_capture> pending;
    std::jthread worker;
    static uint64_t now_ns() { return std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::steady_clock::now().time_since_epoch()).count(); }
    bool private_root() const {
        struct stat s{};
        return !lstat(root.c_str(), &s) && S_ISDIR(s.st_mode) && s.st_uid == getuid() && !(s.st_mode & 077);
    }
    void run(std::stop_token stop) {
        while (!stop.stop_requested()) {
            bool want = false, active = false;
            if (private_root()) {
                int fd = open((root / "hands-frame-headset.lease").c_str(), O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_NONBLOCK);
                if (fd >= 0) {
                    struct stat s{}; std::array<std::byte, 9> bytes{};
                    uint64_t stamp = 0, now = now_ns();
                    if (!fstat(fd, &s) && S_ISREG(s.st_mode) && s.st_uid == getuid() && !(s.st_mode & 077) && s.st_size == 9 && read(fd, bytes.data(), bytes.size()) == 9) {
                        std::memcpy(&stamp, bytes.data(), 8);
                        want = stamp <= now && now - stamp < 1000000000;
                        active = want && bytes[8] == std::byte{1};
                    }
                    close(fd);
                }
            }
            enabled = want; frames = active;
            std::optional<from_headset::headset_capture> job;
            { std::lock_guard lock(mutex); job.swap(pending); }
            if (!want || !active) unlink((root / "hands-frame-headset.jpg").c_str());
            if (job && want && job->request_id == request && private_root()) {
                auto text = std::as_bytes(std::span(job->status.data(), job->status.size()));
                publish_hands_frame(root / "hands-frame-headset.status", {}, text);
                if (active && !job->jpeg.empty()) {
                    std::array<std::byte, 40> header{};
                    std::memcpy(header.data(), "WVRHFJ01", 8);
                    auto put = [&](unsigned at, uint64_t value, unsigned count) { for (unsigned i=0; i<count; ++i) header[at+i] = std::byte(value >> (i*8)); };
                    put(8,1024,4); put(12,1024,4); put(16,uint64_t(now_ns()) - uint64_t(job->age_ms)*1000000,8);
                    float half = 41.f * 3.14159265358979323846f / 180.f;
                    for (auto [at,value] : {std::pair{24u,-half},{28u,half},{32u,half},{36u,-half}}) put(at,std::bit_cast<uint32_t>(value),4);
                    publish_hands_frame(root / "hands-frame-headset.jpg", header, job->jpeg);
                }
            }
            std::this_thread::sleep_for(std::chrono::milliseconds(50));
        }
        unlink((root / "hands-frame-headset.jpg").c_str());
        unlink((root / "hands-frame-headset.status").c_str());
    }
public:
    headset_capture_bridge() {
        if (auto runtime = std::getenv("XDG_RUNTIME_DIR")) root = std::filesystem::path(runtime) / "wivrn-pro";
        worker = std::jthread([this](std::stop_token stop) { run(stop); });
    }
    std::optional<to_headset::headset_capture_control> control() {
        uint64_t now = now_ns(); bool want = enabled.load(), active = frames.load();
        if (want == last_enabled && active == last_frames && now-last_control < 500000000) return {};
        if (want && !last_enabled) request = now;
        last_enabled=want; last_frames=active; last_control=now;
        return to_headset::headset_capture_control{.request_id=request.load(),.enabled=want,.frames=active};
    }
    void submit(from_headset::headset_capture value) {
        if (!enabled || value.request_id != request || value.age_ms > 250 || value.status.size() > 160 || value.jpeg.size() > 256*1024) return;
        if (!value.jpeg.empty() && (value.jpeg.size()<4 || value.jpeg[0]!=std::byte{0xff} || value.jpeg[1]!=std::byte{0xd8} || value.jpeg[value.jpeg.size()-2]!=std::byte{0xff} || value.jpeg.back()!=std::byte{0xd9})) return;
        std::lock_guard lock(mutex); pending = std::move(value);
    }
    void reset() { enabled=false; frames=false; request=0; last_enabled=false; last_control=0; std::lock_guard lock(mutex); pending.reset(); }
};
}
