// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <string_view>
namespace wivrn {
// Maximum samples per footprint axis. Unsupported values retain the safe off mode.
constexpr int layer_filter_samples(std::string_view mode) {
    if (mode == "balanced") return 2;
    if (mode == "quality") return 4;
    return 1;
}
}
