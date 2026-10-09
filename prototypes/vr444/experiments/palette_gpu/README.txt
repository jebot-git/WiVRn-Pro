RPT1 palette expansion directly to GPU images — offline shader branch

This combines the compression team's RPT1 format and fully validated tile index
with the prior direct-image GPU oracle. It is a distinct opt-in experiment, not
a live WiVRn codec, compositor, networking or headset change. RPT1 preserves the
exact decoded RGB444 pixels of its input; Q8 fixtures preserve already quantized
VR444 pixels, not losslessness relative to the original rendered scene.

Three arms share the same RPT1 expansion shader: output buffer then ordinary
SRGB image copy; direct UNORM imageStore then SRGB view; direct integer UINT
imageStore then SRGB view. Every mode preserves alpha255 and separate eye images.
Raw RGB24, constant RGB, two/four/sixteen-color packed palettes and bounded runs
are supported. One128-lane workgroup expands a tile. Palette indices are read
directly from packed bytes. Runs use a shared-memory inclusive prefix scan and
per-pixel binary search. The run path has three256-word shared arrays (3072bytes)
and no subgroup, FP16, video or other optional shader capability requirement.
The shader writes only actual edge-tile pixels using each tile's indexed width.

Admission and immutable data
rgb_palette::inspect performs complete header/body CRC and tile grammar checks
for BOTH eyes before poisoning an image, writing mapped inputs, or submitting any
GPU work. The index is ten u32 values per tile: payload offset/length/mode,
tile x/y/width/height, palette count, index-data offset, index bits. Static C++
layout/endian checks match the GLSL storage layout. Fixtures own immutable packet
copies through preflight, copy, execution and readback; an index is never reused
with another packet. GPU byte reads have an additional packet-size bound. Shader
geometry/record guards supplement CPU validation and do not replace it.

Only compact RPT bytes, their index and packet-size values are copied for the
timed GPU path. No CPU pixel reconstruction occurs there. CPU reference decode
and legacy VR444 preprocessing are outside timing and used solely to construct
and check fixtures. The shared oracle retains expanded-slot-sized input capacity
for code reuse, so this experiment demonstrates lower copied input bytes, not
minimal allocation size. All three arms use that same allocation policy.
Indices are fully re-created by each timed preflight; their allocation/CRC/grammar
cost is included in validation_ms. The uncompressed RPT input is assumed already
available; optional WPC1/LZ4, network receipt, and server encoding are not timed.

Independent controls
The byte/image/single-EOTF oracle from direct_image_gpu was extracted into
gpu_oracle.hpp with its default legacy input layout unchanged. The original
legacy decoder executable is rebuilt/rechecked alongside this branch. RPT uses
the alternate ten-word index stride and supplies its own validated input copies.
Every image is first poisoned; raw optimal-image bytes must equal the independent
CPU RGBA oracle. Every direct arm's sampled float bits must equal the ordinary
SRGB buffer-copy arm on that same GPU. The established ideal OETF roundtrip must
preserve each original8-bit code, and alpha must remain exactly1. There is no
cross-device exact-float requirement or direct-arm-specific color tolerance.
All malformed right-eye checks must leave the submission counter unchanged.

Build and CPU-only tests
  bash prototypes/vr444/experiments/palette_gpu/build.sh
  prototypes/vr444/experiments/palette_gpu/build/palette-gpu-host \
    prototypes/vr444/experiments/palette_gpu/build/shaders \
    prototypes/vr444/experiments/rgb_palette/fixtures \
    --extra-goldens prototypes/vr444/experiments/palette_gpu/fixtures --cpu-only --full
  python3 -m unittest discover -s prototypes/vr444/experiments/palette_gpu -v
Both host and Android API29 ARM64 compile with warnings as errors. Android is
compile-only while headset tests are suspended. The shader modules target Vulkan
1.1 and are checked with spirv-val. No new optional extension is required; the
shared oracle's explicit --format-list option is still capability gated.

Host-only GPU qualification
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/palette_gpu/build/palette-gpu-host \
    prototypes/vr444/experiments/palette_gpu/build/shaders \
    prototypes/vr444/experiments/rgb_palette/fixtures \
    --extra-goldens prototypes/vr444/experiments/palette_gpu/fixtures \
    --full --format-list --corpus EXPORTED_RPT_DIRECTORY > RESULT.json
  python3 prototypes/vr444/experiments/palette_gpu/audit_palette.py RESULT.json
Corpus .rpt files must have neighboring .rgba reference files from the compression
team's bounded exporter. Captured inputs remain local and are not committed.
Select only a host GPU with --device N. This executable rejects software devices.

Coverage
84 default generated stereo fixtures retain distinct eye contents, Q1/Q8 and
per-tile1/8/17/1024 reference quantization, seven edge/ramp shapes, noise, checker,
text-like strokes and constant/mixed tiles. --full adds twelve576x640 fixtures.
Six independent hand-authored compression-team goldens cover every RPT mode.
Four more independently written run fixtures force one256-pixel run,129 and192
run records across the128-lane workgroup boundary, and17x17 partial tiles. They
are generated by make_run_fixtures.py without the C++ encoder. Their hashes and
expected raw RGBA are tracked. Thirty-eight prepared corpus cases (24 captured
eyes and14 synthetic images) are exported through RPT and matched exactly to
their existing CPU references. Imported single-eye cases are duplicated as two
eyes; generated stereo fixtures
carry distinct eye data. A full corpus run therefore covers144 fixtures.

Observed 2026-10-09 qualification
Intel Arc A770 passed the full144-case suite with image-format-list enabled and
all three output arms; the default extension path passed94 cases. The refactored
legacy decoder independently passed96 generated cases through its default path.
All144 CPU fixtures passed ASan/UBSan with leak detection disabled because the
environment runs under ptrace. Both executable targets compiled, four palette
SPIR-V modules passed spirv-val, and six palette/shared-audit unit tests passed.
See qualification-20261009.json for local evidence and source/binary hashes.

Timing and promotion
Measured arm order rotates by case and repetition after one ordinary-SRGB-first
warmup set. validation_ms includes both CRC/grammar/index preflights;
input_copy_ms includes both actual RPT/index copies; stereo_gpu_ms brackets
dispatch/copy/sample-ready transitions; pipeline_wall_ms includes recording,
submission and fence; oracle_ms reports separate sampled/raw readback checks.
Image poisoning, reference decode and oracle work alter cache/queue state even
though excluded from dispatch timing. This first qualification allows other-team
CPU correctness work. The audit explicitly makes no performance claim.

The compression team's original CPU measurements include server preprocessing
and optional LZ4 wire tradeoffs. Those costs do not disappear when client expansion
moves to a shader. A useful comparison still needs a controlled same-input full
pipeline measurement, sustained graphics contention, actual client driver feature
and image-format checks, and eventual wearer/optical testing after suspension.
No desktop result establishes headset support, sub-15 ms submission, or photons.
