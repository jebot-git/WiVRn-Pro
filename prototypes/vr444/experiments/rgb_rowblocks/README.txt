RRB1 independent exact RGB row blocks (2026-10-09)

Status and decision
Offline, single-eye codec prototype; no production transport, stereo publication,
GPU implementation or headset work. This branch preserves byte-exact opaque RGB
already decoded from VR444, including existing Q8 quantization. It does not make
lossy Q8 source images lossless. All input alpha must be255. Raw high-entropy
fallback retains all RGB bytes. No color transform, subsampling or new quantizer.

Splitting a frame into independently compressed row groups gives bounded inverse
predictor dependencies and independent LZ4 blocks. This could support parallel
workers or earlier block preparation in a later transport, but neither is measured
here. Current decode receives the whole immutable frame and validates every block
before any output writes. Total CPU cost on the captured groups remains above
VR444+plainLZ4. Smaller blocks generally lose compression and increase client cost.
Preserve the approach as an explicit tradeoff, not a promoted latency solution.

Wire, little endian
Outer header32 bytes:
0 magic'RRB1';4 version1;5 log2(block_rows), allowed4..7 (16/32/64/128 rows);
6..7 zero;8 widthu32;12 heightu32;16 block_countu32;20 complete_wire_sizeu32;
24 CRC32 of the offset table;28 CRC32 of header bytes0..27.
Block count must equal ceil(height/block_rows). Axes are1..4096; total pixels are
at most4,194,304. There are at most256 blocks. The table immediately follows and
contains block_count entries of offsetu32,lengthu32. Offsets are absolute and must
describe exactly contiguous blocks in order, with no gaps, overlap or trailing
bytes. The first offset is32+8*block_count. Maximum wire is12,593,184 bytes.

Each entry is a complete independent RRF1 frame, specified in
../rgb_rowfilter/README.txt. Its width must equal the outer width and its height
must be block_rows except for the exact final remainder. Mode0 is raw RGB24;
mode1 is LZ4 RGB24; mode2 is LZ4 up-predictor residuals. Each block has its own
32-byte RRF1 header, header/body CRCs, exact length and strict LZ4 grammar checks.
Mode2 resets at the first row of EVERY block: first row unchanged, later rows
subtract prior row bytewise modulo256. There is no cross-block/frame dictionary.
The encoder retains a compressed block only when at least64 bytes smaller than
that block's raw RGB. Plain/up/adaptive policies have the same semantics as RRF1.
Adaptive attempts both compressors and its full cost is counted.

Compared with global RRF1, outer/index/extra inner headers add exactly40 bytes per
block before compressed-body differences: (32+8*N+32*N)-32 =40*N. Full raw fallback
is3*width*height+32+40*N bytes. The frame may exceed the independent VXF/VXI256KiB
stereo transport profile; an integration must reject or define another bounded
profile, never silently reduce quality. CRCs detect corruption, not authentication.

API and failure containment
inspect(wire) returns a fully checked Index with width,height,block_rows and at
most256 Block descriptors: row,height,mode,offset,length,raw_offset,raw_bytes.
offset/length refer to the complete embedded RRF frame, raw_offset/bytes to the
combined row-major RGB buffer. It validates outer geometry/table and every block's
header, CRC and grammar before returning any index.
unpack_rgb(wire,workspace) performs all bounded LZ4 decompressions before returning
the full buffer. Mode2 regions still contain independent row residuals; modes0/1
contain RGB. For a valid nonaliasing call, malformed input clears workspace.rgb.
decode_into additionally restores each predictor domain and expands alpha255;
all decompressions finish before caller-owned RGBA is written. Wrong output size
or a malformed late block leaves the whole caller output unchanged. Public APIs
require nonaliasing buffers and immutable input for the call. An asynchronous
consumer must retain the immutable wire/residual lease through completion.

This format does not carry frame/eye/session identity or a deadline. A consuming
transport must validate those separately, admit both eyes, and publish only after
both complete; partial block progress is never an independently publishable eye.
No receiver deadline is changed by this codec.

All lengths/counters are derived from bounded dimensions before allocation. Peak
RGB payload is12MiB, final caller RGBA at most16MiB, wire at most12,593,184 bytes,
plus bounded compression workspaces for one at-most4096x128 block and256 metadata
descriptors. Vector capacity retention and allocator overhead are separate from
payload bounds; this prototype does not claim a fixed total process memory cap.

Correctness
ASan/UBSan:1,345 roundtrips,8,473 rejections across10,000 mutations and directed
cases. These include each row-boundary16/32/64/128 and adjacent heights, odd widths,
single-pixel,256-block height4096, all modes/policies, exact scalar/SIMD wire,
per-block residual oracle, truncation, corrupt late blocks, repaired outer and
inner CRC malformed inputs, unchanged output on rejection, alias/bounds failures,
and maximum4MP high-entropy raw fallback. Four independent Python-authored golden
wires use manual LZ4 tokens and zlib CRCs, covering raw odd dimensions, compressed
plain/up blocks, mixed modes and predictor reset into a different final-row color.
Android ARM64 API29 executables cross-compile; no device/runtime test occurred.
Independent compositor-team code review found no bounds or partial-output blocker
in canonical table parsing, block geometry or pre-output validation. Its contract
nit was addressed by clarifying that alias precondition rejection preserves prior
workspace state; malformed nonaliasing reconstruction clears residuals.

Matched host comparison
38 inputs =24 captured eyes (12 stereo pairs) +14 synthetic eyes. Every output is
checked against the existing scalar decoded RGBA outside timing. Seven rotating
arms,3 warmups+31 measured repetitions; exact same input hashes before/after.
Source reconstruction from VR444 is counted for all RGB alternatives; wrapper
encoding and client decode are included. Timed decode includes preflight, CRC,
strict grammar, LZ4, predictor and alpha expansion; it also returns bounded block
metadata. No network, GPU upload, compositor, runtime wait or optical timing.
The final batch ran13.69s with other research teams explicitly idle. Ordinary
desktop/services remained; no clock/frequency/thermal/affinity control. Statistics
below are means of per-eye medians, not stereo or pooled percentile predictions.

Captured Q1 group,8 eyes
profile          group wireB   serverms   clientms   server+clientms
VR444plainLZ4         98,239     .067942    .232514       .300764
globalRRFup          55,655     .337585    .187889       .530252
RRB16up              76,943     .311808    .257119       .571165
RRB32up              66,332     .304746    .238580       .545076
RRB64up              60,867     .302509    .228633       .536721
RRB128up             58,298     .304288    .218840       .526827
RRB32adaptive        63,238     .358341    .477041       .839512

Q1RRB128 retains40.66% wire savings vsVR444 but uses4.75% more bytes than global
RRFup. Server is.033297ms faster and client.030951ms slower than globalRRFup; total
CPU remains worse than VR444. Captured Q8 groups also remain negative: RRB32up
server+client .918457/.787203ms versus VR444 .501775/.419620, and wire grows
34.26%/43.29%. RRB32adaptive gains a few bytes but costs more client CPU because
some blocks choose plain RGB compression. Full per-case samples and mode counts
are in results/benchmark.json; all other block sizes are retained.

Reproduce from repository root
  bash prototypes/vr444/experiments/rgb_rowblocks/build.sh
  /tmp/wivrn-rgb-rowblocks-build/test
  python3 prototypes/vr444/experiments/rgb_rowblocks/golden.py /tmp/rrb-goldens --decoder /tmp/wivrn-rgb-rowblocks-build/test
  bash prototypes/vr444/experiments/rgb_rowblocks/build-android.sh
  /tmp/wivrn-rgb-rowblocks-build/bench validation/results/codec-viability-0.8.0/compact/corpus 31 > /tmp/rrb-bench.json
  python3 prototypes/vr444/experiments/rgb_rowblocks/summarize.py /tmp/rrb-bench.json /tmp/rrb-summary.json
  /tmp/wivrn-rgb-rowblocks-build/export validation/results/codec-viability-0.8.0/compact/corpus /tmp/rrb-export > /tmp/rrb-manifest.json

Captured bytes and reconstructed pixels remain local; only tiny independent
fixtures, hashes, measurements and code are committed. Export contains the same38
inputs at four block sizes (152 cases), not152 independent captures.

Primary source
https://github.com/lz4/lz4/blob/v1.10.0/doc/lz4_Frame_format.md
LZ4's frame format distinguishes independent from linked blocks. RRB1 uses custom
framing around independent raw LZ4 blocks and is not an LZ4 frame-format decoder.
https://www.w3.org/TR/png-3/#9Filter-types
The up predictor subtracts the corresponding prior-row byte modulo256. RRB1
resets per block and does not implement PNG framing or claim PNG compatibility.
