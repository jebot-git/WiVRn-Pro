Exact RGB row filtering with independent LZ4 frames (RRF1)
========================================================

Status: host-only research, not integrated or promoted. This branch compares plain
RGB24/LZ4 and a reversible previous-row byte predictor with existing VR444/LZ4.
It preserves current decoded RGB bytes, including Q8's already quantized values;
it does not restore the original pre-quantization image. The observed wire saving
on Q1 captures is accompanied by worse host CPU latency. Q8 capture wire size grows.
No headset, production socket, Android deployment or optical test is performed.

RRF1 contract
-------------
Independent little-endian 32-byte header, followed by exactly one body:
  0..3 magic RRF1; 4 version1; 5 mode; 6..7 reserved0
  8 width u32; 12 height u32; 16 body length u32; 20 RGB byte length u32
  24 CRC32 of stored body; 28 CRC32 of preceding28 header bytes

Modes: 0 = unfiltered raw RGB24; 1 = LZ4 RGB24; 2 = LZ4 row-up RGB24 residuals.
RGB is interleaved R,G,B, row-major, top to bottom. Input alpha must be255 and
decoded alpha is255. No chroma subsampling, color transform, transfer-function
conversion, quantization, temporal reference or external dictionary occurs.
For mode2, first-row bytes are unchanged. Every later byte is current RGB byte
minus the same-channel byte in the preceding row, modulo256. Reconstruction adds
the already reconstructed preceding-row byte modulo256. Each frame is independent.
This is the PNG Up byte-predictor idea with separate framing/LZ4; RRF1 is not PNG.

Axes1..4096, maximum4,194,304 pixels, raw RGB length exactly3*width*height, maximum
wire12,582,944 bytes. These limits are explicit and narrower than some legacy
VR444 shapes. Raw fallback retains original unfiltered RGB; compressed bodies must
be strictly smaller than raw. Encoder requires at least64 saved bytes and chooses
plain/up/both according to the requested policy. Adaptive selection counts BOTH
attempts. Frame length includes the32-byte header. It may still exceed the legacy
VR444/LZ4 size or a transport's stereo cap; reject/fallback at a future negotiated
outer profile, never silently reduce quality or enlarge transport bounds.

CRC32 is computed over stored body, then header. It detects corruption, not peer
authentication. No full-image CRC pass is added. Complete CRC/shape/length checking
precedes output allocation. LZ4 safe decode alone accepts some nonconforming blocks
(observed offset0); RRF1 therefore also validates the documented block token,
literal/match length, positive backward offset, exact output extent, final literals
and last-match boundary before invoking bounded LZ4 decompression. Invalid source
or grammar never publishes partial RGBA output. Caller-owned output remains
unchanged on malformed-frame rejection. Aliasing/lifetime violations are rejected
API preconditions. Input must remain immutable during decoding.

Workspace storage is reusable and bounded by shape; adaptive encoding can retain
RGB, filtered RGB, two LZ4 scratch arrays and final wire (~63 MB at the maximum
shape). Client unpack retains one RGB/residual array (~12.6 MB maximum), separate
from caller-owned RGBA output (~16.8 MB). There is no progressive image publication.
Session identity, expiry and atomic stereo publication are outer obligations.

API for later shader comparison
------------------------------
inspect(wire) validates complete header/body CRC and compressed grammar and returns
Shape{width,height,mode,raw_bytes}. unpack_rgb(wire,workspace) additionally performs
bounded complete decompression into workspace.rgb, clears it on malformed-frame
failure, and returns Shape. Mode2 bytes remain row residuals; modes0/1 are RGB.
A GPU consumer must own an immutable snapshot/lease through GPU completion and
retain whole-eye and stereo failure containment. inspect alone does not decompress
pixels. The current decode_into reconstructs row residuals on CPU, expands RGB to
opaque RGBA, then returns. No shader, Vulkan integration or target admission claim
is included in this branch.

Build, independent fixtures and export
-------------------------------------
  prototypes/vr444/experiments/rgb_rowfilter/build.sh
  /tmp/wivrn-rgb-rowfilter-build/test
  python3 prototypes/vr444/experiments/rgb_rowfilter/golden.py \
    /tmp/rrf-goldens --decoder /tmp/wivrn-rgb-rowfilter-build/test
  /tmp/wivrn-rgb-rowfilter-build/bench \
    validation/results/codec-viability-0.8.0/compact/corpus 31 > /tmp/rrf-bench.json
  python3 prototypes/vr444/experiments/rgb_rowfilter/summarize.py \
    /tmp/rrf-bench.json /tmp/rrf-summary.json
  /tmp/wivrn-rgb-rowfilter-build/export \
    validation/results/codec-viability-0.8.0/compact/corpus /tmp/rrf-export-new \
    > /tmp/rrf-export-manifest.json

Export requires a new directory and validates every result before writing. Actual
capture pixels stay local; only hashes and four small independent fixtures are
committed. golden.py manually authors raw and two legal LZ4 blocks without the C++
encoder, covering both compressed modes and an odd-width raw row. Independent
modulo-wrap arithmetic checks predictor semantics separately from roundtripping.

ASan+UBSan:136 roundtrips across1/15/16/17/35/65 boundaries, constant/structured/
noise content, all three policies, max4MP,8,352 rejection checks and10,000 mutated
frames (half with repaired CRCs). A repaired CRC may describe different legal RGB;
such inputs are bounded accepted images, not an authentication failure. Explicit
tests reject zero/far LZ4 offsets, incomplete sequences and invalid final literals.
Four independent golden vectors also pass. All38 exported decoded-RGBA hashes
match the corresponding prior RPT export, providing a common pixel oracle.

Measurement interpretation
--------------------------
Corpus has24 captured eyes (8 paired-a Q8,8 paired-b Q8,8 lossless Q1) and14
synthetic cases. It is a tiny static-scene sample, not representative game footage.
Both eyes are kept; statistics remain per-eye rather than a stereo scheduling
prediction. Profiles rotate each iteration with3 warmups and31 samples; cases are
deterministically shuffled. All exact decoded-RGBA comparisons are outside timing.
No quality threshold is relaxed, cases omitted, or high-entropy fallback hidden.

Server transcode includes reconstructing pixels from the same existing VR444
source plus RGB packing, filter/compression trials, framing, CRC and wire copies.
Wrapper encode and source reconstruction are also shown separately. They do not
represent a measured native server renderer or GPU encoder path. Client timing
includes all CRC/grammar/LZ4 checks, row restoration and RGB expansion. Allocation
capacities are reused after warmup. Timing is one uncontrolled-clock i7-12700 host;
collaborators hold work for the brief measurement but ordinary desktop/services
remain. CPU and cache behavior does not predict mobile/headset performance.

See results/summary.json for joint wire/encode/decode results and provenance. Group
timings are means of case p50s, not pooled p95. Q1 up-filter wire falls from98,239
to55,655 bytes including headers (-43.35%) across8 captured eyes. Plain RGB uses
61,060 bytes. Q8 up-filter wire grows27.88% (paired-a) and36.07% (paired-b).
The current host CPU decode and total transcode costs do not justify promoting the
row-up path. Keep this exact, tested representation as a bandwidth/compute tradeoff
for a separate SIMD or GPU reconstruction experiment. Reduced bytes before first
receipt do not themselves establish strict receive-to-submit latency improvement.

Primary references
------------------
PNG byte filtering, especially Up and modulo-byte reconstruction:
https://www.w3.org/TR/png-3/#9Filter-types
LZ4 v1.10.0 documented block grammar, offsets and end-of-block constraints:
https://github.com/lz4/lz4/blob/v1.10.0/doc/lz4_Block_format.md
