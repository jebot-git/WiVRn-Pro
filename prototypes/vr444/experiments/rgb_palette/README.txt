RPT1 direct RGB tile codec experiment (2026-10-09)

Status and motivation
Offline candidate only; no production/live codec or network integration. Headset
testing is suspended. This trades server preprocessing and potentially much larger
wire payloads for a client path without inverse transforms. It operates on the
EXACT decoded RGBA bytes of existing VR444 Q1/Q8 packets. Preserving Q8's already
quantized output is not losslessness relative to its original source image.
No quality reduction, chroma subsampling, temporal reference, sharpening, optical
latency or headset speedup is claimed. Alpha is required to be 255.

The encoder compares independently decodable 16x16 tiles using raw RGB24, constant
color, 2/4/16-color packed palette indices, and RGB run lengths. It selects the
smallest payload, preserving raw RGB as the high-entropy escape. All palette and
run state ends at each tile. Raw escape bounds growth relative to RGB24, NOT
relative to the original VR444 or its LZ4 wrapper. Bandwidth must be assessed
together with encode/decode time; a faster decode with a larger wire is not an
end-to-end success.

Reproduction from repository root
  bash prototypes/vr444/experiments/rgb_palette/build.sh /tmp/rgb-palette
  /tmp/rgb-palette/test
  /tmp/rgb-palette/bench CORPUS_DIRECTORY 31 > /tmp/rgb-palette.json

The benchmark takes a bounded recursive corpus of .payload files, <=512 files and
<=64MiB combined, with <=16MiB per file. Reference codec sources are taken from
the tracked integration source overlay. It does not read a device. The test build
uses ASan+UBSan; benchmark builds use clang++ -O3 and the vendored LZ4 v1.10.0.
It does not enable Vulkan or open a socket.

Wire grammar, little-endian
32-byte header: magic u32=0x31545052 (RPT1), version u16=1, tile edge u16=16,
width u32, height u32, tile count u32, body bytes u32, body IEEE CRC32 u32,
header IEEE CRC32 u32 over the preceding 28 header bytes. Body CRC covers all
tile records. CRC detects corruption and does not authenticate a peer. RPT1 has
no session/eye/frame/warp metadata: a future integration must negotiate a distinct
codec type, wrap immutable stereo identity, match the outer dimensions/color
contract, and publish only after both eyes pass integrity and decode.

Each raster-ordered tile is: payload length u16, mode u8, reserved u8=0,
then payload. Edge tiles contain only actual pixels in row order, with no padding
pixels. Mode 0 is 3*N raw bytes, RGB byte order as the existing packed RGBA u32.
Mode 1 is one RGB triple. Modes 2/3/4 start with a color-count byte (exactly 2,
3..4, or 5..16 respectively), then that many distinct RGB triples, then packed
indices with 1/2/4 bits, low bits first. All indices and trailing zero padding are
checked. Mode 5 is a sequence of (run length minus one u8, RGB triple); total
coverage must equal N, and adjacent identical runs are forbidden. N<=256.

Bounds and failure behavior
Dimensions: 1..4096 per axis, <=4,194,304 pixels. Tile count is derived exactly.
Each payload: 1..768 bytes; at most 65,536 tile records. Maximum packet bound:
3*4,194,304 + 4*65,536 + 32 = 12,845,088 bytes. No trailing data, reserved modes,
reserved fields, short records, invalid palette indices, duplicate palette colors,
nonzero padding or inconsistent run coverage is accepted. Header/body checksums
and all tile grammar are checked before allocating decoded pixels or writing an
existing output span. A bounded tile-index allocation precedes pixel allocation.
decode_into rejects overlap and requires exact output size. Rejected inputs leave
the caller's output unchanged. The decoder cannot expose a partial valid image.

Tests exercise all six modes, Q1/Q8/Q1024 exact decoded-image equivalence, 1x1,
single-row/column, partial tiles, 576x640, maximum pixel count, high entropy,
every truncation of a sample, corruption, CRC-repaired malformed grammar,
reserved fields, bounds, output poisoning, overlap and incorrect destination size.

Timing definitions
The three profiles are VR444+optional LZ4 (existing safe fallback/min64-byte saving),
RPT1, and RPT1+optional LZ4. All use the same captured legacy packet and exact CPU
RGBA oracle. Per-profile operations rotate order each repetition; corpus order is
seeded/shuffled; three warmups precede repetitions. Each timing retains samples.
post_vr444_server_ms starts with a complete serialized legacy packet. Baseline
measures WPC1 compression selection. RPT1 profiles include direct CPU VR444 decode,
tile encoding/CRC, and (when selected) LZ4. That host decode is a real cost of this
implementation and is not hidden. Original GPU encoding and original source RGB
are outside all profiles. client_decode_ms includes optional WPC1 decode/CRC and
either direct VR444 SIMD reconstruction or full RPT1 CRC/preflight/reconstruction
into a persistent private output span. All encode allocations are included; codec
output capacity and LZ4 workspace are reused where the APIs allow it. Comparisons
against the reference image and file IO are outside timing. post_vr444_combined_ms
is the measured sum with no transmission, upload, compositor or runtime work.

This is host evidence. The strict live target is complete-stereo earliest receive
to post-xrEndFrame submission, so the host preprocessing cost is upstream of that
metric while still affecting frame age and throughput. Different wire sizes may
affect receive-to-stereo-complete and upstream queues. None is predicted from this
benchmark. Android performance and representative game quality remain unmeasured.

Initial host evidence (results/host-20261009)
38 matched cases, 31 repetitions. ASan+UBSan: 240 exact roundtrips, 23,346
rejections, 13,000 checksum/grammar mutations, and maximum pixel count passed.
All six tile modes were exercised. Per-case raw timings and input/source/binary
SHA-256 receipts are preserved. These are averages of case medians, not pooled
p95 and not an end-to-end or stereo latency result.

                       VR444+LZ4       RPT1           RPT1+LZ4
paired-a bytes         182,910         390,278        176,415
paired-a client ms       0.420           0.224          0.328
paired-a server ms       0.095           1.562          1.734
paired-b bytes         135,653         314,865        131,858
paired-b client ms       0.353           0.198          0.286
paired-b server ms       0.081           1.431          1.561
lossless bytes          98,239         119,146         23,992

Each captured group has eight eyes; "bytes" is the sum for that group. Plain
RPT1 saves 0.155..0.196ms host client decode per Q8 eye while more than doubling
wire size. Wrapping RPT1 in LZ4 reduces Q8 wire bytes by 2.8..3.6% and saves
0.066..0.092ms client decode but adds 1.48..1.64ms server preprocessing. This first
implementation does not justify live promotion. Initial encoder allocations and
constant-tile scanning remain concrete optimization opportunities. The small
lossless captured scene favors palette encoding much more than these Q8 groups;
do not generalize that reduction to all Q1 images or high-entropy synthetics.

Primary references consulted, 2026-10-09
https://www.w3.org/TR/2025/REC-png-3-20250624/#11PLTE
  Indexed colors and explicit palette/sample bounds are established lossless
  building blocks. RPT1 is a separate tile format; it is not PNG-compatible.
https://qoiformat.org/qoi-specification.pdf
  QOI provides RGB/RGBA literals, runs and indexed colors in a simple sequential
  format. RPT1 uses independent tile palettes, with no frame-spanning pixel cache.
https://github.com/lz4/lz4/blob/v1.10.0/doc/lz4_Block_format.md
  LZ4 is the existing lossless byte-wrapper comparison, not a new image quality
  mode. Vendored implementation and license are in ../native_lz4/vendor.

GPU handoff and independent fixtures
inspect(wire) performs the same complete header/body CRC and tile-grammar checks
as decode, then returns a Layout with width/height and a TileRecord array. Each
record contains absolute byte_offset, byte_length, mode, x/y/width/height,
palette_colors (zero for nonpalette modes), pixel_data_offset (first packed index
byte for palettes; payload start otherwise), and index_bits (1/2/4 or zero).
Offsets apply only to those exact immutable wire bytes. The API returns no partial
layout on failure and allocates no pixels. GPU users must validate the whole eye
before enqueueing work, match trusted outer dimensions, and keep both eye outputs
unpublished until the complete stereo identity is valid. Shader bounds/status and
resource lifetime checks remain necessary even after CPU preflight.

The six tiny golden fixtures in fixtures/ were constructed by golden.py without
using the C++ encoder. They cover every mode, partial tiles and palette tail bits,
and include exact expected little-endian RGBA bytes plus SHA-256 hashes. Verify:
  python3 prototypes/vr444/experiments/rgb_palette/golden.py /tmp/rpt-golden \
    --verify /tmp/rgb-palette/export

For the existing local captured/synthetic corpus, export RPT1 plus independent
existing-VR444-reference RGBA files (no capture files are committed by this tool):
  /tmp/rgb-palette/export CORPUS_DIRECTORY /tmp/rpt-gpu-corpus \
    > /tmp/rpt-gpu-corpus-manifest.json
This validates all 38 prepared cases before writing fixtures. Binary writes are
bounded to decoded RGBA and wire packet sizes, and use explicit little-endian
pixel serialization. The exporter does not measure GPU performance or enqueue
GPU work. inspect/golden additions pass all 240 exact roundtrips, 34,379 rejection
checks, 13,000 mutations and all six independently authored golden decodes.
