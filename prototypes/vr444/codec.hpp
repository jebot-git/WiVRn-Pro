// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include <vector>
#include <string>
namespace vr444 {
constexpr unsigned TILE = 16, SLOT_WORDS = 320;
struct Frame {
    unsigned width, height, q;
    std::vector<uint32_t> slots, lengths;
    std::vector<uint32_t> tile_q; // Empty = v1 uniform quantizer; otherwise v2.
};
Frame encode(const std::vector<uint32_t>& rgba, unsigned w, unsigned h, unsigned q,
             const std::vector<uint32_t>& tile_q = {});
std::vector<uint32_t> decode(const Frame& f);
// Wire format is explicitly little-endian, versioned, and bounded before allocation.
std::vector<uint8_t> pack(const Frame& f);
Frame unpack(const std::vector<uint8_t>& wire);
size_t payload_bytes(const Frame& f);
std::vector<uint32_t> scene(unsigned w, unsigned h, unsigned kind);
double psnr(const std::vector<uint32_t>& a, const std::vector<uint32_t>& b);
void self_test();
}
