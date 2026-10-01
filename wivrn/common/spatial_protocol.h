// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <cstddef>
#include <cstdint>
#include <openxr/openxr.h>

namespace wivrn::spatial {
// Fixed capacities bound allocation and control-channel packet size on both peers.
inline constexpr std::size_t marker_count = 12, entity_limit = 64, boundary_limit = 32;
struct location {
    XrPosef pose{{0, 0, 0, 1}, {0, 0, 0}};
    XrSpaceLocationFlags flags = 0;
};
struct marker {
    std::array<uint8_t, 16> uuid{};
    location pose;
    uint8_t status = 0;
    int32_t result = 0;
    bool persisted = false;
};
struct entity {
    std::array<uint8_t, 16> uuid{};
    std::array<uint8_t, 128> labels{}; // char signedness differs between ARM and x86.
    location pose;
    std::array<XrVector2f, boundary_limit> boundary{};
    uint32_t boundary_count = 0;
    bool boundary_omitted = false;
    bool has_box = false;
    XrVector3f box_offset{}, box_extent{};
};
struct snapshot {
    uint64_t stream = 0, sequence = 0, generation = 0;
    XrTime time = 0;
    bool active = false, supported = false;
    std::array<marker, marker_count> markers{};
    std::array<entity, entity_limit> entities{};
    uint32_t entity_count = 0;
    bool scene_complete = false;
    int32_t scene_result = 0;
    location head;
    bool bounds_available = false;
    XrExtent2Df bounds{}; // STAGE rectangle, not the Guardian polygon.
    bool capturing = false;
    uint32_t accepted = 0, verified = 0;
};
}
