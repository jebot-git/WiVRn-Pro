// SPDX-License-Identifier: MIT
#pragma once
#include <array>
#include <cstdint>
#include <span>
#include <vector>
namespace compact {
struct Frame {
    unsigned width,height,q,count;
    std::vector<uint8_t> wire;
    // offset byte, length[0..10] | mode[11] | q[12..22]
    std::vector<std::array<uint32_t,2>> records;
    unsigned constant_tiles=0;
};
// Validates full VRI2 DC-only grammar before any GPU object/submission is used.
// No expanded coefficient slots or decoded RGB are allocated by this parser.
Frame prepare(std::span<const uint8_t> wire);
void self_test();
}
