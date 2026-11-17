// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cstdint>
#include <limits>
#include <stdexcept>

namespace wivrn::packed444
{
// Original Y/Cb occupy the left/right halves of coded Y. Original Cr even/odd
// rows occupy coded Cb/Cr. Geometry is logical until handed to a video encoder.
struct extent
{
    uint16_t width;
    uint16_t height;
    bool operator==(const extent &) const = default;
};
inline extent coded_extent(uint32_t width, uint32_t height)
{
    if (!width || !height || (width & 1) || (height & 1) ||
        width > std::numeric_limits<uint16_t>::max() / 2 || height > std::numeric_limits<uint16_t>::max())
        throw std::invalid_argument("Packed444 requires even nonzero logical dimensions and a representable doubled width");
    return {uint16_t(width * 2), uint16_t(height)};
}
inline extent logical_extent(uint32_t width, uint32_t height)
{
    if (!width || (width & 3) || !height || (height & 1) ||
        width > std::numeric_limits<uint16_t>::max() || height > std::numeric_limits<uint16_t>::max())
        throw std::invalid_argument("Invalid Packed444 coded dimensions");
    return {uint16_t(width / 2), uint16_t(height)};
}
} // namespace wivrn::packed444
