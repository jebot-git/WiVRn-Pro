// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <cmath>
#include <array>
#include <cstdint>
#include <string>
#include <vector>

namespace wivrn::subtitles
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
inline bool valid(const packet & p)
{
    if (p.lines.size() > 16 || !std::isfinite(p.duration) || p.duration < 2 || p.duration > 10 ||
        !std::isfinite(p.scale) || p.scale < .6f || p.scale > 1.5f) return false;
    if (p.visual && !p.lines.empty())
    {
        if (p.lines.size() > 4 || !std::isfinite(p.distance) || p.distance < .5f || p.distance > 10) return false;
        for (auto v : p.capture_pose) if (!std::isfinite(v) || std::abs(v) > 10000) return false;
        float norm = 0;
        for (int i = 3; i < 7; ++i) norm += p.capture_pose[i]*p.capture_pose[i];
        if (std::abs(norm-1) > .02f) return false;
        for (auto v : p.capture_fov) if (!std::isfinite(v) || std::abs(v) > 1.5f) return false;
        if (p.capture_fov[0] >= p.capture_fov[1] || p.capture_fov[3] >= p.capture_fov[2]) return false;
        for (const auto & line : p.lines)
        {
            for (auto colour : {line.foreground, line.background})
                for (auto v : colour) if (!std::isfinite(v) || v < 0 || v > 1) return false;
            auto [x,y,w,h] = line.box;
            for (auto v : line.box) if (!std::isfinite(v)) return false;
            if (x < 0 || y < 0 || w < .01f || h < .01f || x+w > 1.001f || y+h > 1.001f) return false;
        }
    }
    for (const auto & line : p.lines)
        if (line.text.empty() || line.text.size() > 1024 || line.text.find('\0') != std::string::npos ||
            !std::isfinite(line.bearing) || std::abs(line.bearing) > 1.05f || line.age_ms > 12000) return false;
    return true;
}
} // namespace wivrn::subtitles
