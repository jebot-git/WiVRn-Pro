// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "subtitle_protocol.h"
#include <algorithm>
#include <array>
#include <glm/glm.hpp>
#include <glm/gtc/quaternion.hpp>

namespace wivrn::subtitles
{
class scene_overlay
{
    glm::vec3 current_position{};
    glm::quat current_orientation{1,0,0,0};
    glm::vec3 capture_position{};
    glm::quat capture_orientation{1,0,0,0};
    bool tracking = false;
public:
    struct region
    {
        std::string text;
        glm::vec3 position{};
        glm::quat orientation{1,0,0,0};
        glm::vec2 size{};
        std::array<float,3> foreground{}, background{};
    };
    std::array<region,4> regions;
    bool enabled = true;
    bool use_jupiteroid = true;
    int64_t expires = 0;
    void clear() { regions = {}; expires = 0; tracking = false; }
    void pose_at(glm::vec3 position, glm::quat orientation)
    {
        current_position = position; current_orientation = orientation; tracking = true;
        // Once moved, discard rather than making old text reappear when returning.
        if (expires && !aligned()) { regions = {}; expires = 0; }
    }
    bool aligned() const
    {
        return tracking && glm::distance(current_position,capture_position) < .12f &&
            std::abs(glm::dot(current_orientation,capture_orientation)) > std::cos(glm::radians(15.f)/2);
    }
    void receive(const packet & p, int64_t received, int64_t now)
    {
        if (!p.visual || !valid(p)) return;
        regions = {}; expires = 0;
        if (p.lines.empty() || !enabled || !tracking || now-received > 1'000'000'000) return;
        auto & pose = p.capture_pose;
        capture_position = {pose[0],pose[1],pose[2]};
        capture_orientation = {pose[6],pose[3],pose[4],pose[5]};
        if (!aligned()) return;
        use_jupiteroid = p.use_jupiteroid;
        const auto at = received-int64_t(p.lines.front().age_ms)*1'000'000;
        expires = std::min(now+int64_t(p.duration*1e9f),at+int64_t(12'000'000'000));
        const auto & fov = p.capture_fov;
        float left=std::tan(fov[0]), right=std::tan(fov[1]), up=std::tan(fov[2]), down=std::tan(fov[3]);
        for (size_t i=0; i<p.lines.size(); ++i)
        {
            const auto & line = p.lines[i]; auto [x,y,w,h] = line.box;
            glm::vec3 center{std::lerp(left,right,x+w/2)*p.distance,
                std::lerp(up,down,y+h/2)*p.distance,-p.distance};
            regions[i] = {line.text,capture_position+capture_orientation*center,capture_orientation,
                {(right-left)*w*p.distance,(up-down)*h*p.distance},line.foreground,line.background};
        }
    }
    bool visible(int64_t now) const { return enabled && now<expires && aligned(); }
};
}
