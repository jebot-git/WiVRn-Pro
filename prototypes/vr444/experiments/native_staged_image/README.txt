Native staged RRB image helper — implementation milestone
=========================================================

This branch implements a private, borrowed-device image helper for the selected
RRB32 shader route. It does not modify the current CPU upload or native mapped
RGBA controls. LZ4 decompression stays in checked CPU-owned buffers; only a
bounded memcpy enters coherent mapped staging. The helper consumes full RGB
residual pixels, not compressed wire bytes or chroma-subsampled data.

Owned implementation: source-overlay/client/decoder/staged_rowblocks_image.h,
.cpp and .comp. Shared test_decoder, stream, CMake, overlay manifest and generated
patch are intentionally not edited here: codec and compositor owners supply
those call sites, then root reconciles and builds the actual application. These
files alone are a buildable component, not a claim they are already in an APK.

Native adapter contract
-----------------------
1. Both complete WCT envelopes must pass the codec coordinator's identity,
   stereo size cap, RRB CRC/grammar/LZ4 and shape checks. Retain its immutable
   residual lease while adapting index.blocks to Block{row,height,mode,raw_offset}.
2. Construct one Pool per decoder eye/generation from the application's existing
   physical/device/graphics+compute queue family. Load staged SPIR-V words from
   the installed asset, record its effective hash. Generation is nonzero and
   cannot be reused as an identity for a restarted decoder; eye is0 or1.
3. prepare(frame,Input) rechecks all shader bounds/complete coverage, obtains an
   unowned slot, copies checked residual/metadata to private coherent storage,
   and records only its own command buffer. It returns nullopt under pressure.
   Frames strictly advance. Abandoning unsubmitted Pending consumes that frame
   identity but releases its private slot. No image view is exposed yet.
4. Pending.submit(VkQueue) runs under application::get_queue().lock(); release
   that queue lock immediately. Pending.publish(timeout_ns) then observes its
   actual owned fence and yields Lease. The codec worker must complete both
   eyes before the shared pair publication transaction; a failed second eye
   must not leave the first eye independently visible.
5. Native Handle keeps a Lease copy and supplies lease.image(), lease.view(),
   Lease::layout and sampled_rgb_is_linear=true. Keep every consumer copy until
   its associated rendering fence has completed. Existing normal render/reset
   handling keeps current_blit_handles through that fence. Final exception/
   destructor teardown needs a separate explicit drain proof before enabling.

Lease owns slot, buffers, image/views and shared pipeline independently of Pool
or decoder lifetime. The application VkDevice must outlive all leases. Pool is
single-producer; consumer threads may retain/release published Lease copies.
Same queue family only; no queue-family ownership transfer or asynchronous
cross-queue contract is implied. No timeline/sync2 feature is assumed enabled.
Those private-ring compatibility experiments remain a later native feature
enablement/lifetime stage, not a prerequisite or claimed optimization here.

Timeout is not completion. Pending keeps submitted resources and its destructor
waits on its own producer fence; a hung driver requires the root-owned external
process watchdog. Device loss allows teardown. A non-device-lost submission
failure has unknown completion and terminates rather than free possibly used
resources. No application-wide device or queue idle is hidden in this helper.

Color, storage and barriers
---------------------------
Query core Vulkan1.1, graphics/compute family, dispatch/storage/image limits,
UNORM storage/transfer usage and SRGB linear sampling. Image creation queries
the exact combined usage and mutable flag. Unsupported capability throws.
Create R8G8B8A8_UNORM mutable image with a storage-only UNORM view and a
sampled-only R8G8B8A8_SRGB view. VkImageViewUsageCreateInfo restricts SRGB usage;
no unsupported SRGB storage is requested. Store each RGB byte divided by255
and alpha255. Readback must match every reference byte exactly. The sampled
view applies EOTF once; the native shader must not apply it a second time.

Coherent host writes precede queue submission and an explicit HOST_WRITE to
COMPUTE_SHADER/SHADER_READ barrier. Discard previous image contents only after
all previous leases retire. Compute writes transition GENERAL to
SHADER_READ_ONLY_OPTIMAL with visibility for compute and fragment sampling.
The producer fence completes before publication. This is readiness, not a
consumer-retirement substitute. Readback tests temporarily transition to
TRANSFER_SRC and restore sampling layout while exclusively retaining leases.

Relevant official contracts:
https://docs.vulkan.org/refpages/latest/refpages/source/VkImageViewUsageCreateInfo.html
https://docs.vulkan.org/guide/latest/synchronization_examples.html

Qualification scope
-------------------
Run from the worktree root:
python3 prototypes/vr444/experiments/native_staged_image/qualify.py --build /tmp/unique-staged-build --device 0
The runner performs host, ASan/UBSan and Android ARM64 compile; all shader
modules pass spirv-val for Vulkan1.1. Actual host GPU tests reject software
devices, record model/vendor/device/driver/family/API and use external watchdogs.
CPU-only malformed geometry/mode/coverage checks never initialize Vulkan.

GPU cases cover1x1,1x129,17x33,129x65,4095x3,4096x1,1x4096 and576x640;
distinct RGB spatial patterns, eyes and frame identities; raw/plain/up modes;
unequal eye partitions; partial final blocks and packed-byte tails. Two live
slots stay retained while a third is rejected. Releasing one alias cannot
retire another. Completed reuse produces new exact bytes, and image sampling
still works after the Pool is destroyed while leases survive. Unsubmitted
cancellation and stale/double-submit/publication transitions are rejected.

Both eyes' first consumers use actual sampled views. Compare full readback
against independently generated uncompressed RGBA; compare sampled float bits
against the same hardware CPU-upload SRGB reference. Checks include texelFetch
(ideal inverse EOTF must round back to each byte), linear sample centers and
subpixel offsets. Alpha stays exactly1. Deliberately omitting right-eye sampling
leaves NaN poison and fails that same comparison; exchanging eyes fails exact
image bytes. This does not claim ideal cross-driver float equality.

These full-frame synthetic checks conservatively cover all pixels for a later
central10-degree decoded-image test. They are not original-application source,
foveation/warp/defoveation, final app swapchain or visible-headset evidence.
Root owns measured footprint/source quality admission. Native swapchains
currently lack XR_SWAPCHAIN_USAGE_TRANSFER_SRC_BIT; raw swapchain copy capture
requires an explicitly supported usage and acquired-image lifetime through
copy completion before release. CPU/GPU component equality cannot establish
physical displayed pixels, sustained cadence or sub15 latency.
