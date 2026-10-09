// SPDX-License-Identifier: MIT
#pragma once
#include "../latency_decode/direct.hpp"
namespace bandrgb {
using direct::Shape;using direct::Stats;
// A headerless row-major tile-record band with explicit validated shape. This
// API validates that shape and the complete local record grammar itself; its
// caller separately proves the band's identity within a full original frame.
// Packed R,G,B bytes represent the original decoder's opaque RGB output.
// Output is private until successful return: late malformed coefficients can
// throw after earlier tiles were written. Inputs/output must not overlap.
Stats decode(std::span<const uint8_t>wire,const Shape&shape,std::span<uint8_t>output,bool simd);
const char* vector_backend();
}
