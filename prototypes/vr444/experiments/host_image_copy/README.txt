Queried Vulkan host image copy into reused stereo SRGB images

This opt-in host-only experiment implements VK_EXT_host_image_copy with ordinary
tightly packed RGBA input. It compares the same checked RRF1 SIMD CPU decoder
feeding a staging-buffer image copy or synchronous host image copies. It changes
no live renderer, transport, codec wire format or headset behavior. An extension
name advertised by a desktop GPU is never treated as headset support.

Admission and feature gates
The Vulkan 1.1 prototype explicitly requires and enables VK_EXT_host_image_copy,
VK_KHR_copy_commands2 and VK_KHR_format_feature_flags2. It queries hostImageCopy
before enabling that feature. Core-only promotion without those advertised names
is deliberately unsupported by this implementation. Missing extension/feature,
format, layout or combined image-usage support exits as unsupported (status 2),
never as a passed/skipped arm. Other failures exit 1; only a complete passing
result can enter the audit. Incomplete JSON from a failed run cannot pass.

The SRGB optimal image must support HOST_IMAGE_TRANSFER plus the oracle's sample
and transfer features. The exact created combination is HOST_TRANSFER | SAMPLED |
TRANSFER_SRC | TRANSFER_DST, queried for every shape. Source layouts must contain
TRANSFER_DST_OPTIMAL and GENERAL; destination layouts must contain GENERAL and
SHADER_READ_ONLY_OPTIMAL. The extension performance hints and complete observed
layout lists are retained for every case. Performance hints never substitute for
correctness qualification or measured downstream costs.

Synchronization and lifetime
Both eye packets complete CRC, strict LZ4 grammar, decompression and shape checks
before any mapped write, image poison or host image call. Both CPU RGBA outputs
remain owned throughout the synchronous host copies and subsequent submission.
The reusable images are first cleared by a GPU poison submission. A transfer-write
to HOST_READ memory barrier makes those GPU writes available to host transitions;
the observed fence establishes completion before either image is touched by the
host. A fence alone is not used as the availability proof.

Each image transitions on the host from TRANSFER_DST_OPTIMAL to GENERAL, receives
one full host copy, and transitions to SHADER_READ_ONLY_OPTIMAL. Copy flags remain
zero: this is ordinary RGBA, not the opaque swizzled HOST_IMAGE_COPY_MEMCPY mode.
The next queue submission publishes host writes to device accesses. An explicit
HOST_WRITE to SHADER_READ barrier is conservative; the otherwise empty readiness
submission retains the same second observed-fence endpoint as the staging arm.
Both paths therefore have two timed submissions, including poison. Any partial
host Vulkan failure permanently invalidates the session; no later command can
consume a partially updated pair through this harness.

The shared Session API is opt-in, with prior callers retaining ordinary device
copies. Its host_upload entry requires completed, host-available poison images
and validates both dimensions before the first transition. Native tests reject
an upload without this availability state and a malformed right-eye shape without
changing the left image state. Every fixture also corrupts its right RRF packet
and verifies rejection before left mapped-memory mutation or submission.

Qualification and interpretation
The 170-fixture suite retains every previous row-filter boundary, all four manual
RRF/LZ4 goldens, both maximum 2048x2048 cases and the 38-source export. Generated
eyes differ; imported single-eye references are duplicated for qualification.
Every optimal image matches independent RGBA bytes exactly, alpha remains opaque,
and sampled float bits equal the same-physical-device staging-copy baseline.
The inverse ideal EOTF must still recover each byte within half a code. Reuse
poisons both images, byte readbacks and float sample output before each iteration.

This branch establishes feature, byte, color, admission and reuse correctness.
Its timing observations are not a coordinated performance comparison. Host-copy
optimalDeviceAccess and identicalMemoryLayout may be false; a later comparison
must retain actual downstream sampling costs as well as whole upload readiness.
Server preprocessing, RRF wire costs, network, compositor and optical freshness
remain outside this prototype. Android builds are compile-only. Private logical
devices, diagnostic poison and uncontrolled desktop workload differ from a native
client. No Vulkan validation layer is enabled.

Observed qualification, 2026-10-09
Arc A770 and UHD 770 each passed all 170 fixtures in both arms: 340 recorded and
340 warmup invocations per device. Both advertised and enabled all required EXT
dependencies, supported the exact combined usage and required layouts, and
reported all three performance hints false. The maximum inverse-EOTF error was
0.193304 code values. This is a same-device comparison, not cross-driver float
equality. ASan/UBSan passed 26 paired profiles in both arms with three warmups and
one recorded repetition (208 invocations); no diagnostics were emitted.

Twelve sanitizer capability-gate negatives and six Python audit tests with 39
negative mutations passed. Host and Android API 29 ARM64 builds passed. Rebuilt
ordinary row-filter (170 fixtures, three output modes) and original direct-image
(96 fixtures, three modes) regressions passed. All 12 prefix/sample SPIR-V hashes
remain identical to the previously spirv-val-qualified shaders. Concurrent
correctness work was allowed, so none of these observed clock values are promoted
to performance evidence. Numeric coverage, capabilities and immutable source
hashes are recorded in qualification-20261009.json.

Reproduce
  bash prototypes/vr444/experiments/rowfilter_gpu/build.sh
  bash prototypes/vr444/experiments/host_image_copy/build.sh
  ASAN_OPTIONS=detect_leaks=0 \
    prototypes/vr444/experiments/host_image_copy/build/gate-test
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/host_image_copy/build/host-image-copy-host \
    prototypes/vr444/experiments/rowfilter_gpu/build \
    prototypes/vr444/experiments/rgb_rowfilter/fixtures \
    --qualify --corpus EXPORTED_RRF_DIRECTORY > QUALIFICATION.json
  python3 prototypes/vr444/experiments/host_image_copy/audit.py QUALIFICATION.json \
    prototypes/vr444/experiments/rgb_rowfilter/results/corpus-hashes.json
  python3 -m unittest discover -s prototypes/vr444/experiments/host_image_copy -v
Repeat with --device 1 for the second explicitly identified host device. For an
instrumented paired run, replace the executable with host-image-copy-asan, use
EXPORTED_RRF_DIRECTORY as the second positional argument and --reps 1 without
--qualify. ASan leak detection is disabled under the current ptrace environment.

Independent compositor-team review found no blocker in complete-pair admission,
queried usage/layouts, clear-to-host availability, owner lifetimes, ordinary RGBA
pointer interpretation, readiness submission or sticky failure handling.

Primary Vulkan references
Extension dependencies and scope:
  https://docs.vulkan.org/refpages/latest/refpages/source/VK_EXT_host_image_copy.html
Host transition layout and GPU-write availability requirements:
  https://docs.vulkan.org/refpages/latest/refpages/source/VkHostImageLayoutTransitionInfo.html
Host-copy coherence and next-submission visibility:
  https://docs.vulkan.org/refpages/latest/refpages/source/vkCopyMemoryToImage.html
