// SPDX-License-Identifier: GPL-3.0-or-later
#include <array>
#include <cassert>
#include <cmath>
#include <iostream>
#include <optional>
#include "history.h"
#include "polynomial_interpolator.h"

// This test isolates history from network time synchronization. All samples
// have an already stable zero offset; the real history/interpolator are used.
XrTime wivrn::clock_offset::from_headset(XrTime timestamp) const
{
    assert(stable && b == 0);
    return timestamp;
}

struct scalar_sample { float value = 0; };
struct scalar_history : wivrn::history<scalar_history, scalar_sample> {
    using history::add_sample;
    scalar_sample interpolate(const scalar_sample & a, const scalar_sample & b, float t)
    {
        return {a.value * (1 - t) + b.value * t};
    }
    scalar_sample extrapolate(const scalar_sample & a, XrTime) { return a; }
};

int main()
{
    constexpr XrTime start = 1'000'000'000;
    scalar_history history;
    wivrn::clock_offset offset{.b = 0, .stable = true};
    history.add_sample(start, start, {0}, offset);
    history.add_sample(start + 1, start + 20'000'000, {10}, offset);
    for (int quarter = 0; quarter <= 4; ++quarter) {
        auto [produced, sample] = history.get_at(start + quarter * 5'000'000);
        assert(std::abs(sample.value - quarter * 2.5f) < 0.00001f);
        assert(produced == start || produced == start + 1);
    }

    using interpolator = polynomial_interpolator<4, true, 1, 4>;
    interpolator rotations;
    auto orientation = interpolator::value_type{0, 0, 0, 1};
    for (int i = 0; i < 3; ++i)
        rotations.add_sample({.production_timestamp = start + i * 10'000'000,
            .timestamp = start + i * 10'000'000, .y = orientation, .dy = std::nullopt});
    // Replace the middle sample with the same physical rotation in the other
    // quaternion hemisphere. Its fit must remain the original constant pose.
    rotations.add_sample({.production_timestamp = start + 30'000'000,
        .timestamp = start + 10'000'000, .y = -orientation, .dy = std::nullopt});
    for (int i = 0; i <= 4; ++i) {
        auto result = rotations.get_at(start + i * 5'000'000);
        assert(result.y);
        assert((*result.y - orientation).norm() < 0.0001f);
    }
    // An older replacement cannot undo the newer measurement.
    rotations.add_sample({.production_timestamp = start + 1,
        .timestamp = start + 10'000'000, .y = orientation, .dy = std::nullopt});
    assert((rotations.get_at(start + 10'000'000).y.value() - orientation).norm() < 0.0001f);
    std::cout << "PASS tracking endpoints/quarters and replacement quaternion sign alignment\n";
}
