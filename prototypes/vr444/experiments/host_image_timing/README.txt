Host image copy: readiness and first sampling consumer

This standalone experiment extends the qualified 80e53b1 host-image-copy branch
with an observed first GPU consumer. Both arms begin with the same checked RRF1
stereo packets and SIMD CPU decoding. The baseline stages RGBA and issues device
image copies; the alternative uses queried/enabled VK_EXT_host_image_copy with
ordinary RGBA, host transitions and a readiness submission. The earlier branch's
feature, combined-usage, layout, color, stereo-admission and lifetime gates remain.

The primary intervals are measured directly:
  * Complete packet-to-image readiness, including diagnostic poison, CPU decode,
    copying, transitions, command recording, query retrieval and observed fences.
  * The outer interval from the same start through a first stereo GPU sampling
    submission and its observed fence, including the host gap between operations.
The second interval is not made by adding marginal medians or percentiles.

The consumer fetches every texel once through the SRGB sampling view and writes
four floats per pixel into the oracle's coherent host-visible output buffers.
Its private command contains both eye dispatches, shader-write availability for
host read/reuse, GPU timestamps and a fence. Query retrieval and host overhead
remain inside its measured wall interval. The readiness path has two submissions
(poison and ready); the first consumer adds one. Timestamp spans overlap their
wall intervals and must not be added to those wall values.

This is a nearest-texel compute consumer, not native rendering, warping, filtering
or runtime composition. Float-buffer traffic can dominate its time; it cannot
establish equivalent performance for other sample patterns. It does reveal the
cost of this particular consumer after each actual image-upload path. The actual
image allocation size, selected memory type/flags and memory-type requirements
are recorded for both eyes and both arms. Host-copy performance hints are kept
separate from measured behavior. The harness retains unused staging/oracle
resources in the host-copy arm, so no allocation saving is claimed.

Correctness cannot be hidden by the later oracle
After the joint clock ends, the first consumer's completed output is copied into
owned host vectors before the independent oracle can overwrite it. Every result
must match the verified same-physical-device CPU-copy sample bits, as well as
exact independent RGBA image bytes, opaque alpha and the original half-code
inverse-EOTF bound. The first baseline is independently color-checked for every
fixture. All later invocations retain the complete oracle; no sample is waived.
The --fault-consumer control deliberately omits the right-eye dispatch and must
fail even though the subsequent independent oracle itself runs correctly.

Both physical host devices run the complete 170-fixture qualification. The paired
sanitizer mode uses all 26 profiles: 12 captured stereo pairs from 24 eye exports,
plus 14 explicitly duplicated-eye synthetic cases. Every malformed right packet
must fail before left input mutation or submission. Host upload without a prior
completed, available poison image and a wrong right-eye shape must also fail
before host image state changes. Existing extension-support negatives remain.

Comparative protocol
For each device separately, every profile has three warmups and 31 recorded
repetitions for both arms. Order rotates by profile and repetition; neither arm
receives predecoded pixels or free CRC/grammar/decompression work. Workspaces and
private Vulkan resources are reused after completion. Full image/color checks
and first-consumer host-vector copies are outside both primary clocks. The
measurement wrapper requires prior explicit research-team idle coordination and
verifies binary, all 12 shader files, manifest and 76 corpus files remain unchanged.
The existing desktop workload remains uncontrolled.

Observed results, 2026-10-09
Both devices passed all 170 fixtures in both arms with one warmup and one recorded
invocation: 1,360 exact qualification invocations across the two devices. The
paired ASan/UBSan pass checked 208 invocations without diagnostics. Eight Python
audit tests cover 52 negative mutations, and the native omitted-right-consumer
control fails as required. Host and Android API 29 ARM64 builds passed.

The coordinated Arc batch ran from 08:18:25 to 08:20:44 UTC (138.63 seconds); UHD
followed from 08:20:44 to 08:23:02 (138.44 seconds). Both other research teams were
explicitly idle throughout. All 3,224 measured invocations and 312 warmups passed;
binary, shader and corpus hashes remained unchanged. The receipt preserves every
per-case p50, p95 and maximum for readiness, joint wall, consumer wall and consumer
device span, plus actual allocation properties and source hashes.

Captured medians of per-pair medians, milliseconds for complete stereo:
                         Readiness                 Ready + first consumer
  Device / group         Staging    Host copy      Staging    Host copy
  Arc A770 / Q1           3.084       2.904          4.231       4.002
  Arc A770 / Q8           3.202       3.061          4.344       4.172
  UHD 770 / Q1            4.539       4.334          5.237       5.071
  UHD 770 / Q8            4.638       4.447          5.378       5.174
Each pair median has 31 observations; Q1 has four captured pairs and Q8 eight.
These summaries are not pooled percentiles, confidence intervals or medians of
paired differences. Diagnostic poison remains inside both primary intervals.

The joint summary differences favor host copy by approximately 0.17 to 0.23 ms
in this batch. The consumer device-span summaries are about 0.682 ms in both Arc
arms and 0.423/0.419 ms for UHD staging/host copy. This particular consumer shows
no material host-copy access penalty in the recorded summaries, despite queried
performance hints being false. It does not prove equivalent access for other
sampling patterns, native rendering or different drivers, nor establish a
production VR444 gain. The modest same-format host-stage improvement is retained
as an opt-in result; no headset or end-to-end path is promoted.

Independent compositor-team review found no blocker in dispatch limits, image
readiness, shader-to-host visibility, query/resource reuse, saving consumer output
before the oracle, same-device baseline comparison or the joint outer clock.

Reproduce
  bash prototypes/vr444/experiments/rowfilter_gpu/build.sh
  bash prototypes/vr444/experiments/host_image_timing/build.sh
  python3 -m unittest discover -s prototypes/vr444/experiments/host_image_timing -v
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/host_image_timing/build/host-image-timing-host \
    prototypes/vr444/experiments/rowfilter_gpu/build \
    prototypes/vr444/experiments/rgb_rowfilter/fixtures \
    --qualify --corpus EXPORTED_RRF_DIRECTORY > QUALIFICATION.json
  python3 prototypes/vr444/experiments/host_image_timing/audit.py QUALIFICATION.json \
    prototypes/vr444/experiments/rgb_rowfilter/results/corpus-hashes.json
Repeat with --device 1 for the second explicitly identified host device. A paired
run instead uses EXPORTED_RRF_DIRECTORY as the second positional argument and
--reps 31 without --qualify. The measurement wrapper measure.py records coordinated
conditions, hashes and the full per-case readiness/consumer/joint distributions.
Android builds are compile-only; host support is not headset support. No headset,
network, native renderer or Vulkan validation layer is operated by this harness.

The same-format RRF client stage excludes server preprocessing, transport, native
compositor and optical freshness. RRF's Q8 wire growth remains a separate negative.
No host-only result from this experiment can establish a sub-15 ms headset gate.
