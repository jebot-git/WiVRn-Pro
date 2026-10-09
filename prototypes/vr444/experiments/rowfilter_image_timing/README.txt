Checked RRF1 packets to ready stereo images: CPU and GPU comparison

This standalone host experiment compares the current SIMD CPU decoder followed
by an RGBA staging-buffer-to-SRGB-image copy with six GPU alternatives: one,
four or sixteen columns per workgroup, each writing a UNORM or UINT image with
an SRGB sampling view. Every arm begins with the same immutable complete RRF1
packets and ends with both optimal images in shader-read layout after an observed
fence. No native client, network, compositor, renderer or headset is operated.

The CPU baseline uses the compression team's published 895e9ef SIMD helpers.
Runtime backend selection is reported, including any scalar fallback. Both eyes
first finish checked CRC, strict LZ4 grammar and decompression. Then the CPU arm
restores mode-2 row differences and expands RGB to opaque RGBA. GPU arms upload
the decompressed RGB/residual bytes and perform those final operations in their
previously qualified prefix shaders. Neither arm can change mapped input or
image state before complete stereo admission and dimension checks.

Timing and ownership
Each path retains its own CPU workspaces and Vulkan resources across iterations.
The CPU RGBA vectors and pipeline contexts are allocated before timing; warmups
establish steady-state residual capacities for both CPU and GPU. The measured
outer interval includes source admission, per-frame CPU work/allocations if any,
diagnostic poison, upload, command recording, submission, observed fence waits,
query retrieval and remaining host overhead through image readiness. The GPU
does not receive a prevalidated buffer for free. The CPU does not receive a
predecoded image for free.

Both paths use two timed submissions per stereo pair: poison and image readiness.
The poison interval is included and separately reported. Subtracting that measured
interval per invocation can describe the remaining observed work, but does not
remove poison's cache/traffic effects or predict production cost. Readback and
the exact image/color oracle are outside the outer clock. Component device spans
overlap pipeline wall time and must not be added to it. Statistics from different
component distributions also must not be added together.

The CPU arm stages eight pixel bytes per stereo pixel location (four per eye);
GPU arms stage six (three per eye), plus final uint padding and four uint metadata
writes per pair. The reported uploaded_pixel_bytes field deliberately counts
pixel data only. All upload work, including metadata/padding, remains in the wall
interval. Host memory is required to be visible/coherent. CPU image copies use
HOST_WRITE -> TRANSFER_READ visibility followed by TRANSFER_WRITE -> SHADER_READ
image visibility and an observed fence. GPU paths retain their qualified shader
barriers. Private resources are retained until completion and reused only after
their fences. No cross-queue or compositor ownership transfer is claimed.

Coverage and oracle
The qualification mode uses all 170 row-filter fixtures: generated distinct eyes,
four manual RRF/LZ4 goldens, explicit row/column boundaries, maximum 4MP images,
and the complete 38-case export. Each of the seven arms must reject a damaged
right-eye packet before changing the left mapped-input sentinel or submitting.
Every valid image must match independent RGBA bytes exactly. Its sampled float
bits must equal the same-physical-device CPU-copy baseline, with the original
half-code inverse-EOTF bound and exact alpha one. The GPU prefix logic remains
unchanged from 67d6e2d.

For timing, the 24 captured eyes are paired by exact directory/frame/eye suffix;
14 synthetic images explicitly use duplicated eyes. All 38 sources therefore
produce 26 profiles: 12 real captured stereo pairs and 14 synthetic profiles.
Every arm has three warmups and 31 measured repetitions. Arm order rotates by
case and repetition. Every output still undergoes the independent oracle after
timing. No synthetic/high-entropy case is removed. The audit binds exact source
coverage, pair identity, dimensions, wire bytes, backend, seven-arm interleaving,
staged pixel bytes, submission counts, finite observations and exactness gates.

Shared-helper changes
The image oracle now separates pipeline readiness from verification, while its
original execute wrapper still performs verification by default. Input buffers
gain TRANSFER_SRC usage for the CPU copy baseline. Row-filter fixtures were
extracted unchanged into fixtures.hpp. The qualification receipt in rowfilter_gpu
is historical evidence bound to 67d6e2d; this branch records its own rebuilt
regressions and current source hashes. No shader feature or wire format changes.

Observed qualification and coordinated batch, 2026-10-09
All 170 fixtures passed in all seven paths on Arc A770: 1,190 measured qualification
invocations plus 1,190 warmups. The paired 26-profile ASan/UBSan pass checked all
seven paths, with three warmups and one recorded repetition (728 exact invocations).
That instrumented run was briefly paused for another team's timing and overlapped
an ordinary regression near its end; none of its clock values are performance
evidence. No sanitizer diagnostics were emitted; leak detection was disabled in
the ptrace environment. Host and Android API 29 ARM64 builds passed. Rebuilt
row-filter 170-case and original direct-image 96-case regressions passed. Five
audit tests cover 25 negative mutations, including exact qualification boundary
identity rather than fixture count alone. Shader hashes remain unchanged.

The native batch ran from 07:37:33 to 07:45:20 UTC (467.32 seconds), with both
research teams explicitly idle throughout. Full out-of-clock color verification
made elapsed batch time much longer than the initial estimate; the idle hold was
explicitly extended. All 5,642 measured invocations passed byte/color/admission
checks. All source files matched their committed export hashes before and after.
Existing desktop processes and GPU workload were uncontrolled.

Captured results: median of per-pair medians, milliseconds for complete stereo
readiness including diagnostic poison. Each pair median has 31 samples; Q1 has
four capture pairs and Q8 has eight. These are not pooled percentiles or confidence
intervals. Per-case p50, p95 and maximum values are retained in the receipt.

  Path                       Q1        Q8
  CPU SIMD RGBA copy        3.094     3.237
  GPU 1-column UNORM        2.929     3.057
  GPU 1-column UINT         2.885     3.005
  GPU 4-column UNORM        2.728     2.885
  GPU 4-column UINT         2.740     2.913
  GPU 16-column UNORM       3.057     3.168
  GPU 16-column UINT        3.001     3.139

The four-column UNORM arm has the lowest group summary in this batch. Its summary
medians differ from the CPU arm by about 0.366 ms for Q1 and 0.351 ms for Q8. This
is an observed host-stage improvement against the same-format SIMD CPU baseline.
It does not establish a stable UNORM-versus-UINT winner, a production VR444 gain,
or a headset/end-to-end improvement. All seven arms, all synthetic cases and the
negative encoder experiments remain in the evidence; no arm is promoted here.

Reproduce
  bash prototypes/vr444/experiments/rowfilter_gpu/build.sh
  bash prototypes/vr444/experiments/rowfilter_image_timing/build.sh
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/rowfilter_image_timing/build/rowfilter-image-host \
    prototypes/vr444/experiments/rowfilter_gpu/build \
    prototypes/vr444/experiments/rgb_rowfilter/fixtures \
    --qualify --corpus EXPORTED_RRF_DIRECTORY > QUALIFICATION.json
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/rowfilter_image_timing/build/rowfilter-image-host \
    prototypes/vr444/experiments/rowfilter_gpu/build \
    EXPORTED_RRF_DIRECTORY --reps 31 > RESULT.json
  python3 prototypes/vr444/experiments/rowfilter_image_timing/audit.py \
    RESULT.json prototypes/vr444/experiments/rgb_rowfilter/results/corpus-hashes.json
  python3 -m unittest discover -s prototypes/vr444/experiments/rowfilter_image_timing -v

Interpretation limits
This measures an isolated same-format client image stage on a host GPU with
static inputs. The existing desktop GPU workload remains uncontrolled even when
the research teams are idle. Separate private logical devices back the arms,
and the diagnostic poison/submission pattern is not the production render path.
The Q1 wire benefit, Q8 wire growth, server preprocessing costs, transport caps
and observed compositor selection timing remain separate requirements. A client
image-stage improvement alone cannot establish sub-15 ms or optical freshness.
Android builds are compile-only; host extension/format support is not a headset
capability claim. No Vulkan validation layer is enabled in these host runs.

An independent compositor-team static review found no blocker in complete-pair
admission, staging bounds, the CPU copy's visibility/layout transitions, lifetime
or outer-clock accounting. Synchronization reference:
  https://docs.vulkan.org/guide/latest/synchronization_examples.html
