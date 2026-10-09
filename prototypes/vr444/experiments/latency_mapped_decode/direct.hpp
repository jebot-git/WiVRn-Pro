// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>
namespace direct {
struct Shape {uint32_t width,height,q,tiles,version;};
struct Stats {size_t constant_tiles=0,mixed_tiles=0;};
Shape preflight(std::span<const uint8_t> wire);
// Output must be private/unpublished until return. A malformed coefficient may
// throw after earlier tiles were written; it never returns a partial frame.
Stats decode(std::span<const uint8_t> wire,const Shape& shape,std::span<uint32_t> output,bool simd);
const char* vector_backend();
}
