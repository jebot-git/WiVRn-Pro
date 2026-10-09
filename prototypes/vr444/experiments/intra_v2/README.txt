Experimental VR444 intra-tile serialization, preparation version 0.7.0

This host-only prototype preserves the existing quantized coefficients and
therefore the decoded RGB exactly. It does not change quantization, perform
sharpening, run GPU work, alter the live WiVRn codec, or claim better visual
quality. It is a candidate format experiment, not a negotiated live transport.

Implementation
intra_v2.cpp/.hpp encode a validated existing vr444::Frame into a separate VRI2
wire format. decode() validates that format and expands it back to the existing
Frame for the CPU reference decoder. The adaptive policy compares five bounded
per-tile modes, choosing the smallest serialized payload:
  0. Legacy coefficient bytes, unchanged fallback.
  1. Constant/DC-only: Y in 8 bits, Co+255 and Cg+255 in 9 bits each (4 bytes).
  2. Coefficients in groups of 8, byte-aligned tile payload.
  3. Coefficients in groups of 16, byte-aligned tile payload.
  4. Coefficients in groups of 32, byte-aligned tile payload.
The compact group grammar retains zero/common/dense/sparse choices, but sparse
masks use actual group width (including the final short group). Adaptive mode
parses and tries all three group granularities and costs substantial CPU time.

The preferred simpler candidate is --dc-only: recognize all-zero AC tiles and
store compact DC, with legacy fallback for everything else. A three-word legacy
tile is only a necessary condition: the code explicitly verifies all 48 group
flags are zero before compacting it. No entire RGB reconstruction is required
in the encoder. This encoder, like legacy pack(), expects a validated/produced
Frame; the decoder fully validates every incoming candidate stream.

Wire format (experimental, little-endian)
28-byte header: u32 magic 0x32495256 ('VRI2'), version=1, width, height, global q,
tile count, flags (bit0 means a per-tile quantizer map; other flags rejected).
For each raster-ordered 16x16 tile: u16 ((payload_byte_length << 3) | mode),
optional u16 tile q, then the payload. Reserved modes 5..7 are rejected.
Dimensions are at most 4096 per axis and 8,388,608 pixels; tile count must match.
Each tile is at most 1280 payload bytes. All record boundaries/flags/tags/lengths
are checked before allocating the fixed-size legacy slots. Decoding loops have
fixed tile/channel/coefficient bounds; no unbounded entropy parsing or recursion.
DC/AC ranges, padding and trailing bytes are checked. Partial-edge tile behavior
is inherited unchanged from the existing transform. Per-tile q maps roundtrip.

This version adds four frame-header bytes relative to VR44. Each chosen tile
payload is no bigger than its original payload, so total expansion is bounded
by four bytes per frame. A production design should add a frame-level unchanged
VR44 escape if even that expansion is undesirable. Standalone VRI2 has no
checksum; a future transport must retain the immutable frame/eye/warp identity
and integrity envelope, and negotiate a distinct payload version/kind. Existing
VR44 decoders must never receive VRI2 silently. This experiment is stateless
across frames and does not introduce predictive frame dependencies.

Validation and measured results
See validation/results/codec-optimization-prep-0.7.0/intra/ from workspace root.
The final benchmark.json is adaptive; benchmark-dc-only.json is the preferred
fast policy. aggregate.json gives byte-weighted ratios and timing aggregates;
provenance.json records source/capture hashes, compiler and CPU. Earlier
measurements are retained as initial/before-final files, not the final result.

Both final profiles cover 122 cases: 48 paired-a Q8, 48 paired-b Q8, 14 lossless Q1
captured eye payloads, plus gradients, chroma lines, seeded noise, text-like
strokes, edges and constants at Q1/Q8. Every case restores the original legacy
bitstream exactly and returns the same decoded RGBA. Q8 is equivalence to the
already-lossy decoded image, not a new claim of losslessness to the source.
Q1 retains the existing lossless source result. The captures are a small
hello_xr scene and do not replace a representative VR game corpus.

Byte-weighted reductions versus the actual captured serialized payloads:
                     DC-only               Adaptive groups
paired-a Q8           32.44%                    35.14%
paired-b Q8           30.59%                    33.56%
lossless Q1           30.25%                    32.45%

DC-only host serialization: approximately 0.176–0.179ms median of per-case
medians on those groups, versus approximately 0.025ms for legacy pack.
Adaptive serialization: approximately 1.31–1.68ms, and its noise cases take
roughly 27–29ms while saving less than 1%. Do not promote adaptive CPU grouping
into the low-latency path. DC-only retains most observed savings at much lower
cost, but is still slower than legacy packing. The primary agent's independent
LZ4 wrapper experiment is a separate stronger CPU baseline, not included in
these timing/size claims. Twenty-four exact DC-only candidates are exported
with a manifest under intra/dc-only-candidates/ for a separately measured
combined experiment. The primary agent subsequently measured that exact subset:
882,808 original bytes; 630,576 DC-only; 416,802 original+LZ4; 415,390 DC-only+LZ4.
Thus DC-before-LZ4 saves only another 0.339% relative to LZ4 alone on this subset;
the gains must not be multiplied. These are matched-subset size results, not the
same corpus subset as the primary's earlier wrapper benchmark. The wrapper
roundtrip timings exclude generating the DC-only input. See sibling result
packet-compression/combined-dc-lz4.json. This supports pursuing direct GPU
constant-tile filling, not stacking extra CPU passes for negligible size gain.

Synthetics expose the limit: DC-only's nonconstant noise/gradient-Q1/edge cases
mostly retain legacy bytes plus four header bytes. Fully constant cases shrink
20,184 to 8,668 bytes, about 57.06%. Adaptive grouping modestly reduces other
patterns but neither candidate makes high-entropy Q1 noise small. No reduction
in source information, quantizer step, or visual-error tolerance was made.

Timing method: one warmup then five measurements per operation/case, single
CPU benchmark process, C++ steady_clock, clang++ -O2, allocations included.
Host serialization starts from an existing Frame; initial RGB transform/GPU
encoding is excluded in both pack measurements. Decode-RGB times include
unpack and reconstruction. Experimental decode currently rebuilds legacy
slots before the old CPU decoder and adds about 1.2ms on captured inputs; this
is a correctness oracle, not an optimized direct decoder. Individual timings
are in JSON. No CPU pinning, thermal control or statistical confidence claim.
No Quest/Arc GPU, network, optical latency or real-time improvement measured.

Sanitizers: ASan+UBSan pass exact coefficient/RGB roundtrips over odd/partial
sizes and multiple quantizers, q maps, both policies, every truncation of a
sample stream, invalid header/tag/length/q/padding/trailing data, and 3,000 bounded
bit mutations. Valid mutations may represent a different valid image and are
not all required to be rejected. These tests establish bounded parsing under
tested cases, not a fuzzing proof.

GPU portability and next decision
DC-only is the most useful first GPU experiment. The existing encode workgroup
already owns DC/AC values, so it can detect zero AC after quantization and emit
a compact record directly; a decoder can fill a constant tile without inverse
transform. This potential GPU reduction is unmeasured. Keep one workgroup per
tile, bound input length, precompute/validate offsets on the host, and preserve
per-tile q and strict stereo/warp identity. Byte-packed variable records need
careful unaligned word loads and no cross-tile reads. Prefer DC-only+legacy as
an initial format permutation; measure subgroup 8/16/32 modes only if bandwidth
savings justify their divergent control flow and register pressure. Compare
all GPU pixels against the existing CPU decoder before a live A/B. The current
expanded 1280-byte-per-tile staging layout is unchanged by this prototype.

Reproduction (workspace root; writes only scratch binaries/results)
REF=.worktrees/wivrn-codec-tests/common/codec_test/vr444
EXP=prototypes/vr444/experiments/intra_v2
clang++ -std=c++20 -O1 -g -Wall -Wextra -Werror -Wno-missing-field-initializers -fsanitize=address,undefined -fno-omit-frame-pointer -I "$REF" "$EXP/intra_v2.cpp" "$EXP/test.cpp" "$REF/codec.cpp" -o /tmp/wivrn-intra-v2-test
/tmp/wivrn-intra-v2-test
clang++ -std=c++20 -O2 -DNDEBUG -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$REF" "$EXP/intra_v2.cpp" "$EXP/benchmark.cpp" "$REF/codec.cpp" -o /tmp/wivrn-intra-v2-benchmark
/tmp/wivrn-intra-v2-benchmark "$PWD" --dc-only > /tmp/intra-dc-only.json
/tmp/wivrn-intra-v2-benchmark "$PWD" > /tmp/intra-adaptive.json

convert.cpp optionally validates a legacy payload, converts it with DC-only,
verifies exact decoded RGB, and writes a candidate file. It is never invoked by
the live suite. AMD CAS research is in the result directory's amd-cas.txt and
sources-amd-cas.json; CAS is a separate quality-changing postprocess proposal.
