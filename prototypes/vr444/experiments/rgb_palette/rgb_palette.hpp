// SPDX-License-Identifier: MIT
#pragma once
#include <cstddef>
#include <cstdint>
#include <span>
#include <vector>

// Offline exact-RGBA serialization experiment. Not a live WiVRn payload type.
namespace rgb_palette {
inline constexpr uint32_t tile_edge = 16;
inline constexpr uint32_t max_axis = 4096;
inline constexpr uint32_t max_pixels = 4194304;
inline constexpr std::size_t max_wire_bytes = 3u * max_pixels + 4u * 65536 + 32;
using Bytes = std::vector<uint8_t>;
struct Image { uint32_t width, height; std::vector<uint32_t> rgba; };
struct Stats { uint32_t raw=0, constant=0, palette2=0, palette4=0, palette16=0, runs=0; };
struct TileRecord {
    uint32_t byte_offset, byte_length, mode;
    uint32_t x, y, width, height;
    uint32_t palette_colors, pixel_data_offset, index_bits;
};
struct Layout { uint32_t width, height; std::vector<TileRecord> tiles; };

// Input alpha must be 255. All RGB bytes are preserved, with no temporal state.
Bytes encode(std::span<const uint32_t> rgba, uint32_t width, uint32_t height,
             Stats* stats = nullptr);
// Header/body CRC and ALL tile grammar are checked before output allocation.
Image decode(std::span<const uint8_t> wire);
// Preflight also precedes the first output store. Exact destination size required.
// Input/output overlap is rejected. No returned view borrows mutable workspace.
void decode_into(std::span<const uint8_t> wire, std::span<uint32_t> output);
// Full CRC/grammar preflight for GPU consumers; returns only after ALL records
// pass. Offsets refer to the exact input bytes, which must remain immutable.
// No output pixels are allocated. A throwing inspect must enqueue no GPU work.
Layout inspect(std::span<const uint8_t> wire);
}
