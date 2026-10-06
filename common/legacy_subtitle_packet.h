// Legacy wire layout only, retained for installed headset compatibility.
// No producer, renderer, recognition or external API is attached to this packet.
// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace wivrn::legacy_subtitles
{
struct line
{
    std::string text;
    uint32_t speaker = 0; // anonymous session ID, zero means unknown
    float bearing = 0;   // radians right of capture-time head forward; stereo estimate only
    uint32_t age_ms = 0; // capture age on server, for matching client pose history
    bool spatial = false;
    std::array<float, 3> foreground{1,1,1};
    std::array<float, 3> background{0,0,0};
    std::array<float, 4> box{}; // OCR: normalized x, y, width, height (top-left origin)
};
struct packet
{
    std::vector<line> lines; // empty clears all displayed subtitles
    float duration = 5;
    float scale = 1;
    bool use_jupiteroid = true;
    bool visual = false; // independent OCR channel, including clears
    std::array<float, 7> capture_pose{}; // xyz, quaternion xyzw in headset world space
    std::array<float, 4> capture_fov{}; // left, right, up, down
    float distance = 2; // assumed plane depth; no scene depth is available
};
} // namespace wivrn::legacy_subtitles
