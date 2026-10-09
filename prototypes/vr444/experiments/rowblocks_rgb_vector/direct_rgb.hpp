// SPDX-License-Identifier: MIT
#pragma once
#include "../latency_decode/direct.hpp"
namespace directrgbvec {
using direct::Shape;using direct::Stats;using direct::preflight;
// Packed R,G,B bytes representing the original decoder's opaque RGB output.
// Output is private until successful return: late malformed coefficients can
// throw after earlier tiles were written. Inputs/output must not overlap.
Stats decode(std::span<const uint8_t>wire,const Shape&shape,std::span<uint8_t>output,bool simd);
const char* vector_backend();
}
