// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "subtitle_protocol.h"
#include <algorithm>
#include <array>
#include <deque>
#include <glm/glm.hpp>
#include <glm/gtc/quaternion.hpp>
#include <optional>

namespace wivrn::subtitles
{
class overlay
{
    struct pose { int64_t time; glm::vec3 position; glm::quat orientation; };
    std::deque<pose> history;
public:
    struct caption
    {
        std::string text;
        uint32_t speaker = 0;
        glm::vec3 position{};
        glm::quat orientation{1, 0, 0, 0};
        int64_t expires = 0;
        float scale = 1;
        bool spatial = false;
        unsigned colour = 0;
        bool use_jupiteroid = true;
    };
    std::array<caption, 4> captions;
    bool enabled = true;
    void clear()
    {
        captions = {};
        history.clear();
    }
    void pose_at(int64_t now, glm::vec3 position, glm::quat orientation)
    {
        if (!history.empty() && (now < history.back().time || now-history.back().time > 500'000'000)) clear();
        if (history.empty() || now-history.back().time >= 30'000'000)
            history.push_back({now, position, orientation});
        while (!history.empty() && now-history.front().time > 13'000'000'000) history.pop_front();
        for (size_t i = 0; i < captions.size(); ++i)
        {
            auto & c = captions[i];
            if (!c.spatial)
            {
                c.orientation = orientation;
                c.position = position + orientation * glm::vec3(0, -.28f - float(i)*.27f, -2);
            }
        }
    }
    void receive(const packet & p, int64_t received, int64_t now)
    {
        if (!valid(p)) return;
        if (p.lines.empty()) { captions = {}; return; }
        if (!enabled || history.empty() || now-received > 1'000'000'000) return;
        std::vector<line> grouped;
        for (const auto & line : p.lines)
        {
            auto it = std::find_if(grouped.begin(), grouped.end(), [&](const auto & x) { return x.speaker == line.speaker; });
            if (it == grouped.end()) grouped.push_back(line);
            else if (it->text.size() + line.text.size() + 1 <= 1024) it->text += " " + line.text;
        }
        for (const auto & line : grouped)
        {
            auto it = std::find_if(captions.begin(), captions.end(), [&](const auto & c) { return !c.text.empty() && c.speaker == line.speaker; });
            if (it == captions.end()) it = std::min_element(captions.begin(), captions.end(), [](const auto & a, const auto & b) { return a.expires < b.expires; });
            auto at = received - int64_t(line.age_ms) * 1'000'000;
            auto source = std::min_element(history.begin(), history.end(), [&](const auto & a, const auto & b) { return std::abs(a.time-at) < std::abs(b.time-at); });
            const bool spatial = line.spatial && std::abs(source->time-at) < 200'000'000;
            const auto & pose = spatial ? *source : history.back();
            const float bearing = spatial ? line.bearing : 0;
            const auto index = std::distance(captions.begin(), it);
            unsigned colour = line.speaker ? (line.speaker-1)%6 : 0;
            for (unsigned attempt = 0; attempt < 6; ++attempt)
            {
                if (std::none_of(captions.begin(), captions.end(), [&](const auto & c) {
                    return &c != &*it && c.expires > now && c.speaker != line.speaker && c.colour == colour;
                })) break;
                colour = (colour+1)%6;
            }
            *it = {line.text, line.speaker,
                pose.position + pose.orientation * glm::vec3(2*std::sin(bearing), -.28f - float(index)*.27f, -2*std::cos(bearing)),
                pose.orientation * glm::angleAxis(-bearing, glm::vec3(0,1,0)),
                now + int64_t(p.duration * 1e9f), p.scale, spatial, colour, p.use_jupiteroid};
        }
    }
    bool visible(int64_t now) const
    {
        return enabled && std::any_of(captions.begin(), captions.end(), [&](const auto & c) { return !c.text.empty() && now < c.expires; });
    }
};
}
