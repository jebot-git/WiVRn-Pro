// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <stdexcept>

namespace wivrn
{
// Same per-axis percentage as the headset slider. Haar blocks require 64-pixel alignment.
inline std::array<uint16_t, 2> pyrowave_foveated_extent(uint16_t width, uint16_t height, unsigned percent)
{
    if (!width || !height || percent > 80)
        throw std::invalid_argument("Invalid PyroWave foveation dimensions or percentage");
    auto axis = [percent](uint32_t full) -> uint16_t {
        const auto reduced = (full * (100 - percent) + 99) / 100;
        const auto aligned = ((reduced + 63) / 64) * 64;
        if (aligned > 4096)
            throw std::invalid_argument("PyroWave foveated extent exceeds compositor limit");
        return aligned;
    };
    return {axis(width), axis(height)};
}
}
