// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include "spatial_protocol.h"
#include <filesystem>
#include <memory>
#include <optional>
#include <string_view>

namespace wivrn::spatial {
// This service is owned by the WiVRn runtime. Local readers never control XR.
class bridge {
    struct implementation;
    std::unique_ptr<implementation> impl;
public:
    // Empty path: opt in with WIVRN_SPATIAL_BRIDGE=1 in the server environment.
    explicit bridge(std::filesystem::path directory = {});
    ~bridge();
    bridge(const bridge &) = delete;
    bridge & operator=(const bridge &) = delete;
    bool enabled() const { return bool(impl); }
    void publish(const snapshot &, std::optional<XrPosef> stage_from_quest, int64_t sample_ns);
    void invalidate(std::string_view reason);
};
// Shared with the offline transform test. All rotations use OpenXR xyzw.
XrPosef transform(const XrPosef & parent, const XrPosef & child);
}
