// SPDX-License-Identifier: MIT
#pragma once
#include "wpc1.h"

namespace wivrn::codec_test::compression {
inline constexpr size_t max_payload_bytes = 16u * 1024 * 1024;
inline constexpr size_t default_minimum_saving_bytes = 64;

struct Selection {
    std::span<const uint8_t> payload;
    bool attempted;
    bool compressed;
    size_t original_bytes;
    size_t wire_bytes;
};

// One workspace per concurrent operation. Views returned by select/decode borrow
// storage and expire when this workspace is next mutated or destroyed. A raw
// selection instead borrows its caller's input. No state crosses frame boundaries.
class Workspace {
    detail::Workspace buffers;
    static bool aliases(std::span<const uint8_t> input, const detail::Bytes& buffer) {
        if (input.empty() || buffer.capacity() == 0) return false;
        const auto a = reinterpret_cast<uintptr_t>(input.data());
        const auto b = reinterpret_cast<uintptr_t>(buffer.data());
        return a <= b ? b - a < input.size() : a - b < buffer.capacity();
    }
    bool aliases(std::span<const uint8_t> input) const {
        return aliases(input, buffers.wire) || aliases(input, buffers.scratch) || aliases(input, buffers.decoded);
    }
    friend Selection select(std::span<const uint8_t>, Workspace&, size_t);
    friend std::span<const uint8_t> decode(std::span<const uint8_t>, Workspace&);
};

// Call only under an explicit test configuration. A false compressed result is
// the original unwrapped payload: never mark it compressed in the outer envelope.
// Inputs at the 16 MiB envelope limit that cannot fit a WPC1 raw escape are passed
// through without attempting compression. minimum_saving_bytes cannot force growth.
inline Selection select(std::span<const uint8_t> raw, Workspace& workspace,
                        size_t minimum_saving_bytes = default_minimum_saving_bytes) {
    detail::require(!raw.empty() && raw.size() <= max_payload_bytes);
    detail::require(!workspace.aliases(raw));
    Selection result{raw, false, false, raw.size(), raw.size()};
    if (raw.size() > detail::input_limit || raw.size() <= 36 ||
        minimum_saving_bytes >= raw.size() || raw.size() - minimum_saving_bytes <= 36)
        return result;
    result.attempted = true;
    detail::encode(raw, detail::wire_limit, workspace.buffers);
    const auto& wire = workspace.buffers.wire;
    if (wire[5] == 1 && wire.size() < raw.size() &&
        raw.size() - wire.size() >= minimum_saving_bytes) {
        result.payload = wire;
        result.compressed = true;
        result.wire_bytes = wire.size();
    }
    return result;
}

// Return only after exact bounded decompression and the uncompressed CRC pass.
// The caller must not invoke the codec decoder if this function throws. CRC is
// corruption detection, not authentication. The outer envelope supplies identity.
inline std::span<const uint8_t> decode(std::span<const uint8_t> wrapped, Workspace& workspace) {
    detail::require(!workspace.aliases(wrapped));
    workspace.buffers.decoded.clear();
    try {
        detail::require(wrapped.size() > 24 && wrapped.size() <= max_payload_bytes);
        detail::require(wrapped[5] == 1 && wrapped.size() < detail::u32(wrapped, 8));
        detail::decode(wrapped, workspace.buffers);
        return workspace.buffers.decoded;
    } catch (...) {
        workspace.buffers.decoded.clear();
        throw;
    }
}
inline bool hardware_crc_enabled() { return detail::arm_crc_enabled(); }
} // namespace wivrn::codec_test::compression
