// SPDX-License-Identifier: MIT
// Host-only differential fuzzer. Accepted compact frames must have identical
// scalar/SIMD/reference bytes; rejected data may alter only private output.
#include "codec.hpp"
#include "direct.hpp"
#include <algorithm>
#include <cstdint>
#include <cstdlib>
#include <optional>
#include <span>
#include <stdexcept>
#include <vector>

static void check(bool value) { if (!value) std::abort(); }

extern "C" int LLVMFuzzerTestOneInput(const uint8_t* data, size_t size) {
    // The fuzzer's own corpus/CPU budget, not a new codec limit. Production
    // maximum-size cases belong to the separate bounds/guard-page suite.
    if (size > 65536) return 0;
    const std::span<const uint8_t> wire(data, size);
    std::optional<direct::Shape> shape;
    std::optional<vr444::Frame> frame;
    try { shape = direct::preflight(wire); } catch (const std::runtime_error&) {}
    try { frame = vr444::unpack(std::vector<uint8_t>(data, data + size)); }
    catch (const std::runtime_error&) {}
    check(shape.has_value() == frame.has_value());
    if (!shape) return 0;
    check(shape->width == frame->width && shape->height == frame->height && shape->q == frame->q);
    const size_t pixels = size_t(shape->width) * shape->height;
    if (pixels > 4096) return 0;
    std::optional<std::vector<uint32_t>> reference;
    try { reference = vr444::decode(*frame); } catch (const std::runtime_error&) {}
    constexpr uint32_t sentinel = 0x13579bdf;
    for (bool simd : {false, true}) {
        std::vector<uint32_t> storage(pixels + 2, sentinel);
        bool accepted = false;
        try {
            direct::decode(wire, *shape, std::span(storage).subspan(1, pixels), simd);
            accepted = true;
        } catch (const std::runtime_error&) {}
        check(storage.front() == sentinel && storage.back() == sentinel);
        check(accepted == reference.has_value());
        if (accepted) check(std::equal(reference->begin(), reference->end(), storage.begin() + 1));
        // Invalid destination length must fail before any output mutation.
        std::fill(storage.begin(), storage.end(), sentinel);
        bool rejected = false;
        try { direct::decode(wire, *shape, std::span(storage).subspan(1, pixels - 1), simd); }
        catch (const std::runtime_error&) { rejected = true; }
        check(rejected && std::all_of(storage.begin(), storage.end(),
                                     [](uint32_t x) { return x == sentinel; }));
    }
    return 0;
}
