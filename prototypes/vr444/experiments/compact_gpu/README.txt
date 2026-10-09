Compact constant-tile GPU decoder feasibility prototype (2026-10-08)

Scope
Standalone experiment only. No live codec, application, transport, shared shader,
or prior frozen result was changed. Uses the existing experimental VRI2 DC-only
wire format from ../intra_v2. Mode 1 stores a constant tile in four bytes; mode 0
retains a legacy tile payload. Q1 and Q8 output must equal CPU decoding of the
corresponding original VR444 packet, including alpha. This is preservation of an
already quantized decoded image, not a visual improvement or an optical latency
measurement.

Build and run from repository root
  bash prototypes/vr444/experiments/compact_gpu/build.sh
  validation/results/codec-viability-0.8.0/compact/build/compact_gpu_probe \
    validation/results/codec-viability-0.8.0/compact/build/shaders \
    validation/results/codec-viability-0.8.0/compact/corpus 0 5 coop128
The build produces native Linux and Android API29 arm64 binaries, six unmodified
reference shader variants and one compact shader. It needs clang++, Vulkan
headers/libvulkan and Android NDK29 (ANDROID_NDK_HOME overrides the default).
Push the Android binary, shaders directory and corpus directory together; same
arguments apply with the target paths. The parent agent alone operates ADB.

Parser sanitizers
  clang++ -std=c++20 -O1 -g -fsanitize=address,undefined \
    -fno-omit-frame-pointer \
    -I .worktrees/wivrn-codec-tests/common/codec_test/vr444 \
    -I prototypes/vr444/experiments/intra_v2 \
    prototypes/vr444/experiments/compact_gpu/test.cpp \
    prototypes/vr444/experiments/compact_gpu/compact.cpp \
    prototypes/vr444/experiments/intra_v2/intra_v2.cpp \
    .worktrees/wivrn-codec-tests/common/codec_test/vr444/codec.cpp \
    -o /tmp/compact-validator-test
  /tmp/compact-validator-test
Tests cover valid partial dimensions, Q1/Q8/Q1024, per-tile quantizers, every
truncation of a mixed small packet, malformed headers/tile modes/lengths/q/padding,
trailing bytes and 3000 deterministic bit mutations. Accepted mutated packets are
also parsed by the separate intra_v2 oracle. ASan/UBSan passed; no CPU timing is
inferred from sanitizer runs.

Format and bounds
VRI2 version 1 is distinct from the existing live VR444 magic. Header: seven
little-endian u32 values (magic, version, width, height, q, count, flags). Each
record has u16 ((length << 3) | mode), optional u16 tile-q, and length payload
bytes. This prototype rejects the intra_v2 adaptive modes 2-4. CPU preflight
validates the complete packet before allocating the compact wire copy/index and
before any Vulkan instance is created. Coefficient grammar, DC ranges, maximum
dequantized AC magnitude, sparse tail bits and zero padding are validated.
Dimensions <=4096 per axis, <=8388608 pixels, q in1..1024, legacy tile length
12..1280 bytes in multiples of4, constant length4, no trailing bytes.

GPU input is the wire packet plus an eight-byte index per tile: u32 payload byte
offset, u32 containing length bits0..10, mode bit11, q bits12..22; upper bits zero.
There are no 1280-byte expanded coefficient slots. One 128-invocation workgroup
handles a tile. Lane0 bounds-checks and parses; constant tiles directly fill RGB
and skip all inverse transforms. Mixed legacy tiles use a conservative scalar
inverse in lane0, followed by a group barrier and parallel pixel fill. Shared
storage is 6144 bytes plus a status word. Every bit read is bounded against its
record and the storage buffer. Shader status is initialized to0xffffffff per
tile and must become zero; invalid status rejects the entire result. Full RGBA
output starts poisoned and is compared exactly against the CPU oracle.

Persistent coherent mapped buffers are used. Compact wire capacity is allocated
once at the maximum corpus packet size; actual packet plus eight-byte indexes is
copied per frame. Output readback is still full RGBA. The baseline allocates and
copies the existing expanded slots. This experiment measures reduced input
traffic, not reduction of output traffic or all process memory: the harness also
holds both packet formats and the CPU oracle deliberately.

Measurement method
Every case gets one warmup and five measured repetitions of both decoders,
alternating order. Queue completion/fence waits are synchronous. Timestamp range
is TOP_OF_PIPE through BOTTOM_OF_PIPE around dispatch; barriers, mapped copies,
validation and readback are outside that GPU interval but inside appropriate
wall intervals. Compact total includes compact parser/index preparation; baseline
total includes GpuSession::decode's validation/expanded copy/readback but excludes
legacy unpack, performed once before timing. Compilation/session creation is
reported separately. Each case's p50/p95/max and all timing definitions are in
JSON. Reports aggregate the arithmetic mean of case p50 values, not pooled p95.
No simultaneous parent/sibling hardware timing was scheduled.

Corpus and limits
38 cases: eight paired-a Q8, eight paired-b Q8, eight lossless Q1 captures, twelve
576x640 deterministic synthetic scenes (gradient, chroma-lines, seeded noise,
text-like strokes, edges, constant; Q1/Q8), a 17x19 mixed per-tile-q case, and a 1x1
constant. Captures are a small scene from the prior worn live run, not a varied VR
corpus. Corpus files and sources are hashed. There is no optical rig, temporal
quality evaluation, live network timing or stereo presentation measurement here.

Results and promotion decision
See validation/results/codec-viability-0.8.0/compact/README.txt. Both Arc and Quest
passed exact output, but scalar fallback is too slow on mixed scenes. Do not
promote this implementation to the live decoder. The constant fill shortcut and
compact input are feasible; a correct parallel legacy path remains necessary.

Unconditional cooperative diagnostic candidate
  bash prototypes/vr444/experiments/compact_gpu/build.sh \
    validation/results/codec-viability-0.8.0/compact/build-unconditional unconditional
Run its binary against its own shaders directory and the same corpus. The JSON
label is cooperative-unconditional. This candidate initializes all 768 coefficients
for constant tiles and executes the cooperative inverse for every valid tile,
including constants. Thus it preserves compact input but sacrifices constant-tile
transform skipping. Host exact-output gate passed 38 cases times six calls per
path (one warmup and five measurements). An initial conditional cooperative
variant failed; this diagnostic does not establish the precise cause. Keep the
candidate separated from both the measured scalar prototype and live code.

Post-review guard builds and Quest candidate
Both host and Quest unconditional candidates passed the same38-case exact-output
gate. Quest captured-Q8 wall time is slower than baseline; Q1 wall is lower while
GPU time is higher. This remains a standalone feasibility candidate, with no
stable product speedup established.

Source now caps file/corpus allocation before reading and includes the shared
status word in the device memory-limit guard (6148 bytes). Parser and bounded-file
tests passed ASan/UBSan. Measured binaries remain in build/ and build-unconditional/;
reviewed binaries are in build-reviewed/ and build-unconditional-reviewed/.
Reviewed shader SPIR-V hashes exactly match the corresponding measured files.
The reviewed guard-only binaries were compile-checked, without new GPU timing.
See review-guard-receipt.json and pre-review-source-build-manifest.json. Reproduce
reviewed builds by passing these distinct output directories to build.sh with
profile scalar or unconditional. summarize.py regenerates derived aggregates and
current source/binary hashes without modifying any raw evidence.
