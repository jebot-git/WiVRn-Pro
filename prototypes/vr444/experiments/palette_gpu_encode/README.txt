Canonical RPT1 encoding on Vulkan — offline server-stage experiment

This branch moves RPT1 tile classification, record generation, prefix scanning
and body compaction to three compute shaders. It addresses the palette CPU
encoder's additional server preprocessing cost as a separate possible approach.
It preserves the current RPT1 format and exact opaque RGB pixels. Nothing is
integrated with a renderer, live stream, network, XR compositor or headset.

The source is the exact already decoded RGB444/Q1 or Q8 RGBA reference used by
the earlier palette experiments. The prototype uploads this source from CPU
memory. It does not claim a GPU-resident renderer input or eliminate the earlier
VR444 transform. Quantization is outside this encoder, and no EOTF is performed.
Both eyes must validate before any source upload or GPU submission. Source
dimensions, pixel count and alpha255 are checked; the two packets are independently
owned. The single private command buffer and temporary buffers are reused only
after its fence completes. Every invocation poisons intermediates/readback and
clears the compact destination, preventing old output from satisfying the oracle.

Algorithm and canonical identity
encode.comp uses one128-lane workgroup per16x16 tile. Pixels load cooperatively;
lane0 performs bounded palette and run selection, and lanes pack the chosen
record. Palette order is first occurrence in raster order, with1/2/4-bit indices
for2/3..4/5..16 colors. Constant tiles bypass selection. Raw starts as best;
palette replaces raw only when strictly shorter; runs replace the current best
only when strictly shorter. Partial tiles contain actual pixels only. Padding
bits and final word bytes are zero. These are exactly the CPU encoder's rules.
The serial per-tile classification is intentionally simple and remains a likely
optimization target; this experiment does not assume it is faster than CPU.

scan.comp computes exclusive record offsets in256-tile blocks using128 lanes.
compact.comp adds preceding block sums (at most64 for a maximum-size image) and
writes records at exact byte offsets. Adjacent records can share a u32 output
word. Atomic OR combines their disjoint byte fields after an explicit zero fill
and transfer-to-compute barrier. Compute barriers connect each pass; the final
copy has a transfer-to-host visibility barrier and an observed fence completion.
The header/body CRC is assembled on CPU after readback, then complete packet
bytes must equal the CPU canonical encoder and decode to the original RGBA.
No packet leaves this executable, and no unaudited GPU result is promoted.

Resources and capability gates
Shaders target Vulkan1.1 with core32-bit storage atomics and no optional shader
extension. The executable queries physical limits and requires128-lane groups,
at least8192 bytes shared memory, six storage-buffer bindings, adequate storage
ranges/dispatch counts, and a compute queue with timestamps. It rejects software
devices. Feature/extension support on a desktop says nothing about the headset.
Host-visible coherent input/readback and device-local intermediates are required;
the actual selected memory flags are reported. Host image-copy, subgroup size
control,8-bit storage, buffer addresses and shader int64 are not requested.

Build and reproduce
  bash prototypes/vr444/experiments/palette_gpu_encode/build.sh
  prototypes/vr444/experiments/palette_gpu_encode/build/admission-test
  prototypes/vr444/experiments/palette_gpu_encode/build/palette-encode-host \
    prototypes/vr444/experiments/palette_gpu_encode/build/shaders \
    prototypes/vr444/experiments/rgb_palette/fixtures \
    --extra-goldens prototypes/vr444/experiments/palette_gpu/fixtures --cpu-only --full --max-size
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/palette_gpu_encode/build/palette-encode-host \
    prototypes/vr444/experiments/palette_gpu_encode/build/shaders \
    prototypes/vr444/experiments/rgb_palette/fixtures \
    --extra-goldens prototypes/vr444/experiments/palette_gpu/fixtures \
    --full --max-size --corpus EXPORTED_RPT_DIRECTORY > RESULT.json
  python3 prototypes/vr444/experiments/palette_gpu_encode/audit.py RESULT.json
  python3 -m unittest discover -s prototypes/vr444/experiments/palette_gpu_encode -v
The build also compiles Android API29 ARM64, without running or installing it.
No broad software fallback silently passes a blocked host capability.

Coverage and observed qualification,2026-10-09
Default130 fixtures:112 generated stereo Q1/Q8 cases over seven shapes and
eight patterns, eight scan-boundary shapes/patterns, six independent all-mode
goldens and four independent run fixtures. --full adds16 cases at576x640;
--max-size adds constant and high-entropy2048x2048 cases. The38 prepared exports
(24 captured eyes and14 synthetic cases)
produce a full186-case suite. Independent input goldens can be noncanonical;
they supply exact pixels and are canonically re-encoded before GPU comparison.
Generated eyes differ; imported single-eye fixtures are duplicated as a pair.
Tile counts255,256,258,512 and16384 exercise partial/full scan blocks and the
64-block upper boundary. Generated fixtures cover every canonical output mode.

Arc A770 passed186 full fixtures. UHD770 passed130 default
fixtures. Every packet was byte-exact to the canonical CPU encoder and decoded
pixel-exact. CPU186 fixtures passed ASan/UBSan; leak detection was disabled
because the environment runs under ptrace. Host/Android warnings-as-errors
builds, three spirv-val modules and three audit tests passed. Local result and
source/binary hashes are in qualification-20261009.json. Captured pixels stay
local and are not committed. No headset test was performed.
The separate admission test covers seven invalid source shapes/alpha cases,
ten absent/insufficient capability cases and independently specified body/header
assembly. Unknown queried-property data is unsupported, never a positive result.

Costs retained and promotion requirements
stereo_preflight_ms includes full-pair validation plus the redundant per-eye
guards. stereo_upload_ms measures both CPU source copies. GPU timing includes
intermediate poison, compact-buffer clear, all three passes and full-capacity
readback. stereo_submit_wait_ms includes recording/submission/fence completion;
it overlaps GPU timing and must not be added to it. stereo_assembly_crc_ms
includes CPU body assembly and both CRCs. Independent canonical/reference
construction and final correctness comparisons are outside these timings.
One warmup pair precedes each measured pair, and other teams can run host work.
The receipt and audit explicitly make no performance or sub-15ms claim.

The readback currently copies the maximum capacity2*(772*tiles+8), even for
constant images. GPU compaction therefore does not yet reduce transfer volume.
A compact-length readback followed by a second copy, persistent host-visible
output, or a renderer-integrated GPU source are different experiments with
different synchronization/cache costs. Future work must measure those complete
costs under graphics contention against the CPU encoder on identical inputs.
Host submission timing alone cannot establish headset latency or optical age.

High-entropy raw fallback grows over RGB24 by4 bytes per tile plus32 bytes per
eye. Actual uploaded, allocated/read-back and wire byte counts are reported for
every case. This branch does not change any transport cap: large/noisy stereo
RPT1 packets can exceed the separate VXF1 prototype's256KiB ceiling and must
remain rejected or choose an explicit existing fallback. No fixture is silently
discarded to improve a wire-size or timing result.

Primary Vulkan references
  https://docs.vulkan.org/guide/latest/atomics.html
  https://docs.vulkan.org/guide/latest/synchronization_examples.html
The baseline32-bit integer atomics need no optional extension; wider/floating
atomics have separate feature requirements. Compute-to-compute buffer visibility
and host readback are explicit dependencies rather than consequences of dispatch
order alone. This executable uses the corresponding original Vulkan barrier API.
