// SPDX-License-Identifier: MIT
#pragma once
#include <cstdint>
#include <optional>
namespace wivrn::codec_test {
constexpr uint64_t submitted_filter_flags(uint64_t requested, bool enabled, bool projection) {
    return enabled && projection ? requested : 0;
}
struct layer_filter_snapshot {
    uint64_t requested, submitted;
    bool enabled, projection;
    bool operator==(const layer_filter_snapshot&) const = default;
};
// Per stream; emit on first successful submit or actual configuration change.
struct layer_filter_telemetry {
    uint64_t generation = 0;
    std::optional<layer_filter_snapshot> last;
    void reset() { ++generation; last.reset(); }
    bool observe(layer_filter_snapshot value) {
        if (last && *last == value) return false;
        last = value; return true;
    }
};
}
