// SPDX-License-Identifier: MIT
#pragma once
#include "codec.hpp"
#include <array>
#include <cstdint>
#include <span>
#include <vector>
namespace intra_v2 {
struct Stats { std::array<uint64_t,5> modes{}; uint64_t legacy_bytes=0, wire_bytes=0; };
// Independent experimental wire format. Never pass to the live VR444 decoder.
std::vector<uint8_t> encode(const vr444::Frame&,Stats* = nullptr,bool adaptive = true);
vr444::Frame decode(std::span<const uint8_t>);
void self_test();
}
