// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <atomic>
#include <chrono>
#include <cstdint>
#include <mutex>
#include <optional>
#include <string>
#include <vector>
#include <utility>

namespace wivrn
{
// One requested capture and one completed frame; never a frame queue.
struct ocr_frame
{
    std::string metadata;
    std::vector<uint8_t> rgba;
};
class ocr_exchange
{
    std::mutex mutex;
    std::optional<ocr_frame> frame;
    std::atomic<uint64_t> request{0};
public:
    void ask(uint64_t id) { request = id; if (!id) { std::lock_guard lock(mutex); frame.reset(); } }
    uint64_t take_request() { return request.exchange(0); }
    void submit(ocr_frame value)
    {
        std::unique_lock lock(mutex, std::try_to_lock);
        if (lock) frame = std::move(value);
    }
    std::optional<ocr_frame> take_frame()
    {
        std::unique_lock lock(mutex, std::try_to_lock);
        if (!lock) return {};
        return std::exchange(frame, {});
    }
};
}
