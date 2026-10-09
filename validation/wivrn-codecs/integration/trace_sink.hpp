// SPDX-License-Identifier: MIT
// One Sink per producer thread. emit() never allocates, waits or writes to disk.
#pragma once
#include <array>
#include <atomic>
#include <chrono>
#include <cstdint>
#include <fstream>
#include <stdexcept>
#include <string>
#include <thread>
#include <time.h>

namespace wivrn_codec_test {
static_assert(std::atomic<uint64_t>::is_always_lock_free && std::atomic<size_t>::is_always_lock_free,
              "target must provide lock-free telemetry counters");
enum class Stage : uint8_t { render_ready, encode_start, encode_end, send_first, send_last,
    recv_first, recv_last, decode_start, decode_end, present_submit, predicted_display, frame_drop };
struct Event {
    uint64_t frame=0, ts_ns=0, warp_id=0, paired_frame=0, pair_id=0, applied_warp_id=0;
    uint64_t payload_bytes=0, wire_bytes=0;
    uint8_t eye=0;
    Stage stage=Stage::render_ready;
};
inline uint64_t monotonic_ns() noexcept {
    timespec ts{}; clock_gettime(CLOCK_MONOTONIC, &ts);
    return uint64_t(ts.tv_sec)*1000000000ULL+ts.tv_nsec;
}
template<size_t N> class Ring {
    static_assert(N>1);
    std::array<Event,N> slots{};
    alignas(64) std::atomic<size_t> head{0};
    alignas(64) std::atomic<size_t> tail{0};
public:
    bool push(const Event& e) noexcept {
        auto h=head.load(std::memory_order_relaxed), next=(h+1)%N;
        if(next==tail.load(std::memory_order_acquire)) return false;
        slots[h]=e; head.store(next,std::memory_order_release); return true;
    }
    bool pop(Event& e) noexcept {
        auto t=tail.load(std::memory_order_relaxed);
        if(t==head.load(std::memory_order_acquire)) return false;
        e=slots[t]; tail.store((t+1)%N,std::memory_order_release); return true;
    }
};
class Sink {
    Ring<4096> queue;
    std::atomic<uint64_t> lost{0};
    std::atomic<bool> done{false};
    std::ofstream file;
    std::string run, clock;
    std::thread worker;
    bool failed=false;
    static void valid(const std::string& s) {
        if(s.empty() || s.find_first_not_of("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")!=std::string::npos)
            throw std::invalid_argument("trace IDs must be ASCII identifiers");
    }
    void write(const Event& e) {
        static constexpr const char* names[]={"render_ready","encode_start","encode_end","send_first","send_last","recv_first","recv_last","decode_start","decode_end","present_submit","predicted_display","frame_drop"};
        file << "{\"type\":\"event\",\"run_id\":\"" << run << "\",\"clock\":\"" << clock
             << "\",\"stage\":\"" << names[unsigned(e.stage)] << "\",\"frame\":" << e.frame
             << ",\"eye\":" << unsigned(e.eye) << ",\"ts_ns\":" << e.ts_ns << ",\"warp_id\":" << e.warp_id;
        if(e.stage==Stage::send_last) file << ",\"payload_bytes\":" << e.payload_bytes << ",\"wire_bytes\":" << e.wire_bytes;
        if(e.stage==Stage::predicted_display) file << ",\"paired_frame\":" << e.paired_frame << ",\"pair_id\":" << e.pair_id << ",\"applied_warp_id\":" << e.applied_warp_id;
        if(e.stage==Stage::frame_drop) file << ",\"reason\":\"adapter_classified_drop\"";
        file << "}\n";
        if(!file) failed=true;
    }
public:
    Sink(const std::string& path, std::string run_id, std::string domain) : file(path),run(std::move(run_id)),clock(std::move(domain)) {
        valid(run); valid(clock);
        if(!file) throw std::runtime_error("cannot open event file");
        worker=std::thread([this]{
            Event e;
            while(!done.load(std::memory_order_acquire)) {
                while(queue.pop(e)) write(e);
                std::this_thread::sleep_for(std::chrono::milliseconds(2));
            }
            while(queue.pop(e)) write(e);
        });
    }
    Sink(const Sink&)=delete;
    Sink& operator=(const Sink&)=delete;
    bool emit(const Event& e) noexcept {
        if(unsigned(e.stage)>unsigned(Stage::frame_drop) || e.eye>1 || !queue.push(e)) {
            lost.fetch_add(1,std::memory_order_relaxed); return false;
        }
        return true;
    }
    // Call after producer has stopped. Add returned drops to run metadata.
    uint64_t close() {
        done.store(true,std::memory_order_release);
        if(worker.joinable()) worker.join();
        file.flush();
        if(!file || failed) throw std::runtime_error("trace disk write failed");
        return lost.load();
    }
    ~Sink() { done.store(true,std::memory_order_release); if(worker.joinable()) worker.join(); }
};
} // namespace
