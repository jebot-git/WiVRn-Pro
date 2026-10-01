// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <array>
#include <cstdint>
#include <openxr/openxr.h>

namespace wivrn::anchor
{
enum class operation : uint8_t { create, load, erase };
enum class state : uint8_t { unsupported, empty, placing, creating, loading, saving, localized, lost, erasing, error };
struct command
{
    uint64_t request;
    operation op;
};
struct sample
{
    uint64_t stream = 0;
    uint64_t sequence = 0;
    uint64_t generation = 0;
    uint64_t request = 0;
    XrTime time = 0;
    std::array<uint8_t, 16> uuid{};
    XrPosef pose{{0, 0, 0, 1}, {0, 0, 0}};
    XrSpaceLocationFlags flags = 0;
    state status = state::unsupported;
    XrResult result = XR_SUCCESS;
    bool persisted = false;
};
}
