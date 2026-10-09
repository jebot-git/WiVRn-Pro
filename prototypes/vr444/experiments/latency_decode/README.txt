Compact-wire CPU decode viability prototype (0.13)

Purpose
Measure one alternative to the existing coop128dc GPU inverse: decode the same
compact VR444 v1/v2 bytes directly into a caller-owned RGBA buffer. Two variants
share the strict parser and canonical constant-tile shortcut: scalar inverse and
four-lane AArch64 NEON inverse (SSE2 on the host). No wire or quantization changes.
The prototype does not modify the live client/server or frozen experiment builds.

Build and run
  bash prototypes/vr444/experiments/latency_decode/build.sh [OUTPUT_DIR]
  OUTPUT_DIR/probe-host --self-test
  OUTPUT_DIR/probe-sanitized --self-test
  OUTPUT_DIR/probe-host CORPUS 21 [SHADER_DIRECTORY]
Android binary: OUTPUT_DIR/probe-android, built for aarch64 Android API 29.
The parent coordinates device transfer and runs the same CLI on the Quest.
CORPUS is recursively searched for *.payload. The checked-in result corpus is
validation/results/latency-critical-0.13.0/decode/corpus (38 captured final-worn
eyes, compact bytes before outer LZ4; provenance in corpus-manifest.json).
The program appends 84 generated fixtures automatically. Repetitions 1..200;
three warmups per case. Optional SHADER_DIRECTORY must contain the current
coop128dc assets expected by the existing GpuSession. GPU runs are optional.

Reusable interface
Only direct.hpp and direct.cpp are required; they have no Vulkan dependency.
  auto shape = direct::preflight(compact_bytes);
  // Check shape.width/height against the caller's expected stream dimensions.
  // Allocate/reuse exactly shape.width * shape.height uint32_t pixels.
  auto stats = direct::decode(compact_bytes, shape, rgba, true);
true selects NEON on AArch64, SSE2 on x86, scalar elsewhere; false selects scalar.
All functions throw std::runtime_error on malformed input. The output must remain
private until decode returns successfully: a late coefficient error can leave
partially written pixels. Do not submit that buffer after an exception. A decoded
frame is not published here. Bounds include dimensions <=4096, <=8,388,608 pixels,
wire <=16 MiB, exact tile count, no trailing bytes, coefficient grammar/ranges,
zero bit padding, per-tile q, exact output length and input/output nonoverlap.
The supplied shape is rechecked against the wire rather than trusted. Caller must
separately handle stale frame IDs, stereo pairing, scheduling and network CRC.

Algorithm
Read bounded tile records directly instead of allocating/zeroing 320 uint32
expanded slots per tile. A three-word tile takes the fill shortcut only if all
48 AC-present flags and all padding bits are zero; length alone is insufficient.
Otherwise reconstruct the coefficient arrays and run inverse lifting. Column
inverse writes a stack scratch block, row inverse writes back, avoiding extra
copy passes. SIMD arithmetic-right-shift implements the same floor division by
two for negative coefficients; interleaves restore row sample order. Every output
pixel includes alpha=255 and is compared against the existing CPU codec oracle.

Measurement boundaries
reference_cpu_total_ms includes compact unpack, expanded allocation, inverse and
new RGBA allocation. direct_scalar_ms/direct_simd_ms use preallocated reusable
outputs and include the full bounds preflight, grammar parse, inverse and RGBA
stores; output poisoning and exact comparison are outside timed regions. This is
an intentional implementation comparison, not an isolated instruction benchmark.
preflight_only_ms is a separate diagnostic, not a duration to add a second time.
CPU order rotates by repetition. Optional GPU runs follow the CPU variants each
iteration: gpu_total_with_unpack_ms includes unpack, grammar validation, upload,
dispatch, wait and CPU readback through existing standalone GpuSession;
gpu_dispatch_ms is GPU timestamp time. That CPU-output GPU total is not the live
resident-image decode/upload path. No result includes network/LZ4, RGBA GPU upload,
image transition, compositor wait, stereo publication or optical display latency.
Do not infer live latency improvement before those costs are measured on-device.

Correctness coverage
The generated cases cover partial tile dimensions, all-constant and mixed tiles,
noise and checkerboards, per-tile q 1/8/17/1024, and all 48 three-word nonconstant
AC-group positions. Self-test also checks all truncations of a mixed-q fixture,
10,000 deterministic mutated frames, 4,096 constant RGB colors, wrong caller
shape and input/output overlap. Accepted outputs must equal the existing CPU
oracle exactly; rejection acceptance must match for both variants. Every warmup
and timed call is checked. Host ASAN+UBSAN tests are separate from timing.

Risks and next gate
CPU scheduling, frequency and thermal behavior can change the benefit. Dense
content is more costly than this sparse captured scene. CPU RGBA still needs a
GPU upload to the compositor texture; the live GPU backend keeps output resident.
NEON host parity does not establish ARM parity: require Quest self-test and corpus
oracle pass. Standalone viability alone does not justify promotion. A later opt-in
integration needs strict exception cleanup, actual backend telemetry, paired-eye
exactness and bounded worn live comparison against the existing resident backend.
