// SPDX-License-Identifier: MIT
// Exercise the production bounded decoder against a mapped destination with
// inaccessible guard pages. Vulkan upload/fence behavior is tested separately.
#include "direct.hpp"
#include "codec.hpp"
#include <algorithm>
#include <array>
#include <cstring>
#include <iostream>
#include <span>
#include <stdexcept>
#include <sys/mman.h>
#include <unistd.h>
#include <vector>

static void require(bool value, const char* message) {
    if (!value) throw std::runtime_error(message);
}
class MappedOutput {
    void* allocation_ = MAP_FAILED;
    size_t allocation_bytes_ = 0;
    uint8_t* accessible_ = nullptr;
    size_t prefix_bytes_ = 0;
    std::span<uint32_t> pixels_;
public:
    explicit MappedOutput(size_t count) {
        const long page = sysconf(_SC_PAGESIZE);
        require(page > 0, "page size unavailable");
        const size_t bytes = count * sizeof(uint32_t);
        const size_t accessible_bytes = ((bytes + size_t(page) - 1) / size_t(page)) * size_t(page);
        allocation_bytes_ = accessible_bytes + 2 * size_t(page);
        allocation_ = mmap(nullptr, allocation_bytes_, PROT_NONE, MAP_PRIVATE | MAP_ANONYMOUS, -1, 0);
        require(allocation_ != MAP_FAILED, "mmap failed");
        accessible_ = static_cast<uint8_t*>(allocation_) + page;
        if (mprotect(accessible_, accessible_bytes, PROT_READ | PROT_WRITE) != 0) {
            munmap(allocation_, allocation_bytes_);
            allocation_ = MAP_FAILED;
            throw std::runtime_error("mprotect failed");
        }
        prefix_bytes_ = accessible_bytes - bytes;
        std::memset(accessible_, 0xA5, prefix_bytes_);
        pixels_ = std::span(reinterpret_cast<uint32_t*>(accessible_ + prefix_bytes_), count);
    }
    ~MappedOutput() { if (allocation_ != MAP_FAILED) munmap(allocation_, allocation_bytes_); }
    MappedOutput(const MappedOutput&) = delete;
    MappedOutput& operator=(const MappedOutput&) = delete;
    std::span<uint32_t> pixels() { return pixels_; }
    void poison() { std::fill(pixels_.begin(), pixels_.end(), 0x5a5a5a5a); }
    void check_guard() {
        require(std::all_of(accessible_, accessible_ + prefix_bytes_, [](uint8_t x) { return x == 0xA5; }), "prefix changed");
    }
};
int main() {
    try {
        size_t checks = 0;
        constexpr std::array<std::pair<unsigned, unsigned>, 6> dimensions{{{1u, 1u}, {1u, 17u}, {17u, 1u}, {17u, 19u}, {31u, 31u}, {576u, 640u}}};
        for (auto [width, height] : dimensions) {
            MappedOutput mapped(size_t(width) * height);
            for (unsigned pattern = 0; pattern < 4; ++pattern) {
                std::vector<uint32_t> input(size_t(width) * height);
                uint32_t seed = 314159;
                for (unsigned y = 0; y < height; ++y) for (unsigned x = 0; x < width; ++x) {
                    seed = seed * 1664525u + 1013904223u;
                    uint32_t color = 0xff204080u;
                    if (pattern == 1) color = 0xff000000u | (seed & 0xffffffu);
                    if (pattern == 2) color = ((x + y) & 1) ? 0xff000000u : 0xffffffffu;
                    if (pattern == 3) color ^= (x % 16 == 3 && y % 16 == 4) ? 0x203060u : 0;
                    input[size_t(y) * width + x] = color;
                }
                std::vector<uint32_t> qmap(((width + 15) / 16) * ((height + 15) / 16));
                for (size_t i = 0; i < qmap.size(); ++i) qmap[i] = std::array{1u, 8u, 17u, 1024u}[i % 4];
                for (bool per_tile : {false, true}) {
                    auto frame = vr444::encode(input, width, height, 8, per_tile ? qmap : std::vector<uint32_t>{});
                    auto wire = vr444::pack(frame);
                    auto shape = direct::preflight(wire);
                    auto oracle = vr444::decode(vr444::unpack(wire));
                    for (bool simd : {false, true}) {
                        mapped.poison();
                        direct::decode(wire, shape, mapped.pixels(), simd);
                        require(std::equal(oracle.begin(), oracle.end(), mapped.pixels().begin()), "mapped output differs from oracle");
                        mapped.check_guard();
                        // A capture must own its pixels independently of a reused
                        // staging slot, including after a later decode failure.
                        const std::vector<uint32_t> capture(mapped.pixels().begin(), mapped.pixels().end());
                        mapped.poison();
                        require(capture == oracle, "snapshot changed after staging reuse");
                        ++checks;
                    }
                }
            }
        }
        // A bad coefficient in a later tile can change earlier private bytes.
        // No partial image is valid, and a subsequent good frame must replace it.
        auto good = vr444::pack(vr444::encode(std::vector<uint32_t>(32 * 16, 0xff204080u), 32, 16, 8));
        auto bad = good;
        bad.back() |= 0x80;
        auto shape = direct::preflight(bad);
        auto oracle = vr444::decode(vr444::unpack(good));
        MappedOutput mapped(32 * 16);
        for (bool simd : {false, true}) {
            mapped.poison();
            bool rejected = false;
            try { direct::decode(bad, shape, mapped.pixels(), simd); }
            catch (const std::runtime_error&) { rejected = true; }
            require(rejected, "late malformed coefficient accepted");
            require(mapped.pixels()[0] == oracle[0], "late-failure case did not write the first tile");
            mapped.check_guard();
            direct::decode(good, direct::preflight(good), mapped.pixels(), simd);
            require(std::equal(oracle.begin(), oracle.end(), mapped.pixels().begin()), "reuse after failure retained partial output");
            mapped.check_guard();
            ++checks;
        }
        std::cout << "{\"pass\":true,\"checks\":" << checks << ",\"vector_backend\":\"" << direct::vector_backend() << "\"}\n";
    } catch (const std::exception& error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
