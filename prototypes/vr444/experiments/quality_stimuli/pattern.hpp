// SPDX-License-Identifier: MIT
#pragma once
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string_view>
#include <vector>

namespace quality_stimuli {
inline void shape(uint32_t width, uint32_t height) {
    if (!width || !height || width > 4096 || height > 4096 || uint64_t(width)*height > 4194304)
        throw std::runtime_error("fixture shape exceeds nonzero 4096-axis / 4 MP bounds");
}
inline bool known(std::string_view name) {
    return name == "chroma-grid" || name == "rgb-ramp" || name == "fine-detail";
}
// All bytes are opaque, encoded SRGB channel codes. These are application
// source bytes, not a decoder-generated reference or a linear shader output.
inline std::vector<uint8_t> pattern(std::string_view name, uint32_t width, uint32_t height, unsigned eye) {
    shape(width, height);
    if (!known(name) || eye > 1) throw std::runtime_error("unknown pattern or eye");
    std::vector<uint8_t> rgba(size_t(width)*height*4);
    constexpr std::array<std::array<uint8_t,3>,8> colors{{
        {255,0,0}, {0,255,255}, {0,255,0}, {255,0,255},
        {0,0,255}, {255,255,0}, {32,224,96}, {224,32,160}}};
    for (uint32_t y=0; y<height; ++y) for (uint32_t x=0; x<width; ++x) {
        std::array<uint8_t,3> c{};
        const auto sx = eye ? width-1-x : x;
        if (name == "chroma-grid") {
            const unsigned band = std::min(3u, uint32_t(uint64_t(y)*4/height));
            const unsigned period = 1u << band;
            const unsigned phase = ((sx/period)^(y/period))&1;
            c = colors[2*band+phase];
        } else if (name == "rgb-ramp") {
            c = {uint8_t(sx&255), uint8_t(y&255), uint8_t((sx+3*y)&255)};
        } else {
            const unsigned quadrant = (uint64_t(sx)*2 >= width) + 2*(uint64_t(y)*2 >= height);
            const unsigned period = 1u << quadrant;
            c = {uint8_t((sx/period)%2 ? 255 : 0),
                 uint8_t((y/period)%2 ? 255 : 0),
                 uint8_t(((sx+y)/period)%2 ? 255 : 0)};
        }
        if (eye) c = {c[2], c[0], c[1]};
        // One-pixel asymmetric border identifies orientation and eye even when
        // a small fixture has a spatially constant interior.
        if (x == 0) c = {uint8_t(eye ? 17 : 239),31,73};
        else if (x+1 == width) c = {47,uint8_t(eye ? 223 : 19),101};
        else if (y == 0) c = {61,113,uint8_t(eye ? 197 : 29)};
        else if (y+1 == height) c = {uint8_t(eye ? 181 : 43),149,83};
        const size_t i=(size_t(y)*width+x)*4;
        for (unsigned k=0;k<3;++k) rgba[i+k]=c[k];
        rgba[i+3]=255;
    }
    return rgba;
}
}
