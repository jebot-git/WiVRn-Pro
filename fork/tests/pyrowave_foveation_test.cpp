// SPDX-License-Identifier: GPL-3.0-or-later
#include "encoder/pyrowave_foveation.h"
#include "compositor/foveation_mapping.h"
#include <cassert>
#include <iostream>
#include <numeric>

int main()
{
    using wivrn::pyrowave_foveated_extent;
    assert((pyrowave_foveated_extent(1440, 1584, 0) == std::array<uint16_t, 2>{1472, 1600}));
    assert((pyrowave_foveated_extent(1440, 1584, 30) == std::array<uint16_t, 2>{1024, 1152}));
    assert((pyrowave_foveated_extent(1440, 1584, 50) == std::array<uint16_t, 2>{768, 832}));
    assert((pyrowave_foveated_extent(1440, 1584, 80) == std::array<uint16_t, 2>{320, 320}));
    for (auto full : {1440u, 1584u, 2160u, 2376u})
    {
        unsigned previous = 4096;
        for (unsigned percent = 0; percent <= 80; ++percent)
        {
            auto encoded = pyrowave_foveated_extent(full, full, percent)[0];
            assert(encoded <= previous && encoded % 64 == 0);
            previous = encoded;
            if (encoded >= full) continue; // Non-foveated padding uses identity mapping.
            for (float center : {-0.9f, -0.5f, 0.0f, 0.5f, 0.9f})
            {
                std::vector<uint16_t> spans;
                wivrn::foveation_math::fill_param_2d(center, encoded, full, spans);
                // These exact spans are transmitted to the GPU defoveator.
                unsigned output_pixels = 0, restored_pixels = 0;
                for (unsigned i = 0; i < spans.size(); ++i)
                {
                    unsigned ratio = std::abs(int(spans.size() / 2) - int(i)) + 1;
                    output_pixels += spans[i];
                    restored_pixels += ratio * spans[i];
                }
                assert(output_pixels == encoded);
                assert(restored_pixels == full);
                assert(spans[spans.size() / 2] > 0); // Center retains 1:1 sampling.
            }
        }
    }
    for (auto args : {std::array<unsigned, 3>{0, 1584, 30}, {1440, 0, 30}, {1440, 1584, 81}, {65535, 65535, 0}})
    {
        bool rejected = false;
        try { (void)pyrowave_foveated_extent(args[0], args[1], args[2]); }
        catch (const std::invalid_argument &) { rejected = true; }
        assert(rejected);
    }
    std::cout << "PASS: aligned extents and foveation reconstruction across strengths and gaze centers\n";
}
