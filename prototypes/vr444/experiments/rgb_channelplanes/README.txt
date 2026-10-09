RCP1 reversible RGB channel layouts (2026-10-09)

Scope
Independent offline codec experiment, not a production VR444 wire change. The
input is the already decoded opaque RGB from each existing frame. Q8 quantization
is preserved, not undone. The transform is byte-exact and does not change color
space, subsampling, bit depth or alpha. Header corruption and malformed compressed
grammar reject before caller output writes. No headset, transport or GPU work is
performed by this branch.

Decision: retain as a measured negative result. On captured inputs, plane layouts
are larger and slower than interleaved RGB-up. Green-delta interleaving saves only
161 bytes across eight Q1 eyes while materially increasing CPU cost. Neither
variant is promoted to a production or GPU path.

Transforms
Four explicit layouts operate on RGB24 bytes:
0: original interleaved (R,G,B).
1: three contiguous planes, all R then all G then all B.
2: interleaved (G,R-G,B-G), with subtraction modulo256.
3: three planes of G, R-G and B-G, again modulo256.
For layouts2/3, inverse R=(G+(R-G)) mod256 and B=(G+(B-G)) mod256, while G is
unchanged. This is an integer bijection, not a luma/chroma approximation. The
layout may cluster repeated channel values or zero residuals for LZ4, but it also
requires preprocessing and inverse gathering that are counted in the comparison.

Each layout is tried separately with either no row filter or an up predictor.
For interleaved layouts, row stride is3*width bytes. For planar layouts, every
plane is a separate width-byte-row domain: its first row remains unchanged, later
rows subtract the corresponding prior-row byte modulo256. No predictor state
crosses a plane boundary. Decode restores rows in order before undoing the layout.
There is no cross-frame dictionary or dependency.

Wire, little endian
32-byte header: magic'RCP1' at0; version1 at4; mode at5; layout at6; reserved0 at7;
widthu32 at8; heightu32 at12; stored body lengthu32 at16; RGB lengthu32 at20;
CRC32(body) at24; CRC32(header bytes0..27) at28.
Mode0 is original raw interleaved RGB and MUST use layout0, even when an encoder
was requested to try another layout. Mode1 is LZ4 of the declared ordered bytes.
Mode2 is LZ4 of the declared ordered bytes after the up predictor. Only modes0..2
and layouts0..3 are admitted. Each eye is an independent LZ4 block. Stored
compressed length must be strictly smaller than raw; this encoder requires at
least64 bytes savings. High-entropy failure to compress retains all original RGB.
Both dimensions are1..4096; at most4,194,304pixels,12,582,912raw bytes and
12,582,944wire bytes. All sizes are validated before allocation. Input/body length
must exactly match with no trailing bytes. The documented strict LZ4 grammar
preflight from RRF1 is reused before bounded LZ4 reconstruction, including positive
match offsets and exact output size. CRCs are corruption checks, not authentication.

API
encode(rgba,width,height,layout,up,workspace) rejects nonopaque alpha and inputs
aliased to any workspace vector. inspect(wire) validates complete framing, CRCs,
dimensions, layout/mode combination and strict block grammar. decode_into writes
the caller's RGBA only after all checks, full decompression, predictor restoration
and layout inversion succeed. Malformed nonaliasing calls clear private decoded/RGB
buffers; alias/output-cap preconditions reject before changing workspace state.
Input must remain immutable for the call. Helpers reject overlapping transform
buffers and invalid layout/domain geometry.

Peak individual RGB buffers are12MiB; workspace capacity/allocator costs are
separate and several reusable buffers may coexist. This is a payload bound, not
a process-memory cap. The format does not carry frame/eye/session/config identity
or deadlines. A stereo integration must validate those separately, hold both eyes
in private staging and publish only a complete valid pair before its own deadline.
Arbitrary raw stereo images can exceed the VXF/VXI256KiB pair limit and must not
silently lower quality to fit.

Reproduce from repository root
  bash prototypes/vr444/experiments/rgb_channelplanes/build.sh
  /tmp/wivrn-rgb-channelplanes-build/test
  python3 prototypes/vr444/experiments/rgb_channelplanes/golden.py /tmp/rcp-goldens --decoder /tmp/wivrn-rgb-channelplanes-build/test
  bash prototypes/vr444/experiments/rgb_channelplanes/build-android.sh
  /tmp/wivrn-rgb-channelplanes-build/bench validation/results/codec-viability-0.8.0/compact/corpus 31 > /tmp/rcp-bench.json
  python3 prototypes/vr444/experiments/rgb_channelplanes/summarize.py /tmp/rcp-bench.json /tmp/rcp-summary.json

Benchmark contract
Nine rotating arms compare VR444+plainLZ4 with four exact RGB layouts, each plain
and up-filtered. Server time includes reconstruction of existing VR444 source
pixels plus the complete wrapper, including reordering/filtering/raw fallback.
Client time includes CRC/grammar, LZ4, inverse predictor/layout and alpha expansion.
Original decoded RGBA comparisons occur outside timing. The layout0 body is also
checked byte-for-byte against the existing RRF1 encoder for both filter policies.
Corpus counts and final measurements are recorded with their provenance; synthetic
images must not be called captured eyes. No transfer, GPU upload, compositor wait,
optical latency or headset speedup follows from a host codec measurement.

Evidence
ASan/UBSan passes242 roundtrips and8,258 rejections across10,000 mutations and
directed cases, including both axes at1, odd17/35 widths, partial dimensions,
constant/gray/colored/checker/noise patterns, all eight layout/filter profiles,
maximum4MP random raw fallback and maximum4MP compressed planes. A separate
hand-calculated modulo/plane-reset oracle checks wraparound values. Five manual
Python LZ4+CRC golden wires cover planar RGB, planar green differences, independent
plane up prediction, interleaved green differences and raw odd dimensions. Caller
output remains unchanged on malformed rejection. Repaired CRC mutations may be
valid different content; CRC is not authentication. Android ARM64 API29 benchmark
and test cross-compile only; no runtime qualification on a device.

Final38x9x31 batch (three warmups) passed every decoded oracle in22.73seconds.
The38 inputs are24 captured eyes (12 actual pairs) and14 synthetics; frozen source
hashes match before/after. Both other research teams confirmed idle at launch.
CPU is a12th-gen i7-12700 with no affinity/frequency/thermal control; ordinary
desktop/services remained. Values below are means of per-eye medians. Wire totals
sum eight eyes in each captured group; they are not per-frame wire predictions.

Captured Q1 group
profile                 wireB    serverms  clientms  combinedms
VR444plainLZ4            98,239    .068897   .242777    .317999
interleaved RGB plain    61,060    .371428   .306501    .688362
interleaved RGB up       55,655    .443492   .206341    .658525
RGB planes plain        96,262    .686763   .361347   1.047470
RGB planes up           74,674    .759382   .400343   1.161452
green-delta plain        59,714    .529800   .564287   1.091106
green-delta up           55,494    .616950   .397691   1.019355
green-delta planes       94,199    .658035   .359883   1.021035
green-delta planes up    73,475    .727500   .394018   1.132443

Green-delta up saves0.2893% versus RGB-up, only20.125 bytes per eye on average,
while adding.173458ms server and.191350ms client. Separating planes loses the
existing interleaved repetition pattern on this limited corpus; that observation
is not a universal claim about planar compression. Captured Q8 green-delta up is
also negative: paired-a wire235,079 versus RGB-up233,898 and VR444182,910; paired-b
185,838 versus184,585 and135,653. Q8 green-delta client .480426/.441187ms versus
RGB-up .284205/.247801ms. Full samples and all synthetic outcomes are retained.
Generic layout code, copies and gathering are included in these implementation
costs; the experiment does not claim a lower bound on an optimized GPU transform.
The negligible captured wire gain does not justify such an additional path here.

Only source hashes, tiny independent fixtures, code and measurements are committed.
No captured pixel content or frozen acquisition is changed.

Primary source background
https://www.w3.org/TR/png-3/#9Filter-types
The up predictor subtracts corresponding prior-row bytes modulo256. RCP1 uses
custom framing and independent plane boundaries, not PNG encoding.
https://github.com/lz4/lz4/blob/v1.10.0/doc/lz4_Block_format.md
The existing strict LZ4 block grammar is retained. The reversible channel mapping
above is fully specified algebraically and introduces no lossy quantization.
