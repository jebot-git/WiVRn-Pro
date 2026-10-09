// SPDX-License-Identifier: GPL-3.0-or-later
#pragma once
#include <vulkan/vulkan.h>
#include <cstdint>
#include <memory>
#include <optional>
#include <span>

namespace wivrn::staged_rowblocks {
struct Identity {
    uint64_t generation, frame;
    uint32_t eye;
    bool operator==(const Identity&) const = default;
};
struct Block { uint32_t row, height, mode, raw_offset; };
struct Input {
    uint32_t width, height;
    std::span<const uint8_t> rgb;
    std::span<const Block> blocks;
};
// RGB is a completely checked, CPU-owned RRB residual buffer, not wire bytes.
// modes 0/1 contain RGB; mode 2 resets the row predictor at each block.
// The caller completes CRC/LZ4/grammar checks before this boundary. This second
// check proves all shader offsets and complete nonoverlapping output coverage.
void validate(const Input&);

class Pool;
struct Slot;
class Lease {
    std::shared_ptr<Slot> slot;
    explicit Lease(std::shared_ptr<Slot>);
    friend class Pending;
public:
    Lease() = default;
    explicit operator bool() const { return bool(slot); }
    Identity identity() const;
    VkImage image() const;
    VkImageView view() const; // R8G8B8A8_SRGB; sampled RGB is already linear.
    static constexpr VkImageLayout layout = VK_IMAGE_LAYOUT_SHADER_READ_ONLY_OPTIMAL;
    // Consumers MUST retain a copy through completion of every submitted read,
    // including on exceptions/reset. The application device must outlive it.
    // Readback may temporarily transition layout only with exclusive consumer
    // ownership, and must restore `layout` before normal sampling.
};
class Pending {
    std::shared_ptr<Slot> slot;
    explicit Pending(std::shared_ptr<Slot>);
    friend class Pool;
public:
    Pending(Pending&&) noexcept;
    Pending& operator=(Pending&&) = delete;
    Pending(const Pending&) = delete;
    ~Pending();
    // Only this call requires the application's queue lock. Queue must belong
    // to the configured family. No queue submission/wait occurs in prepare().
    void submit(VkQueue);
    // Wait outside the queue lock. Actual owned producer fence gates exposure.
    // Timeout throws without releasing GPU-owned resources or allowing reuse.
    Lease publish(uint64_t timeout_ns);
};
class Pool {
    struct Impl;
    std::unique_ptr<Impl> impl;
public:
    // Borrows the already-created application device; no optional features or
    // extensions are assumed enabled. Requires core Vulkan 1.1. Single producer
    // thread per pool; published leases may be retained on consumer threads.
    Pool(VkPhysicalDevice, VkDevice, uint32_t queue_family, uint32_t width,
         uint32_t height, uint64_t generation, uint32_t eye,
         std::span<const uint32_t> decode_spirv, uint32_t slots = 8);
    ~Pool();
    Pool(const Pool&) = delete;
    // Strictly advancing frame IDs in one decoder generation. Returns nullopt
    // under slot pressure without mapped writes or consuming this frame ID.
    std::optional<Pending> prepare(uint64_t frame, const Input&);
};
} // namespace wivrn::staged_rowblocks
