Independent row blocks reconstructed directly into GPU images

This standalone prototype consumes the compression team's unchanged a7db779 RRB1
wire/API (locally cherry-picked as 816576df). Each packet contains independent
RRF1 blocks of 16, 32, 64 or 128 rows. Every block begins a new row predictor.
The exact existing Q1/Q8 decoded RGB values and opaque alpha are retained. No
native renderer, transport, compositor, headset or wire format is changed.

Shader approach
A 128-lane workgroup owns one row block and 128 adjacent columns. Each lane reads
successive rows of its own column, adds mode-2 residuals modulo 256 and writes
opaque RGBA. Adjacent lanes read adjacent pixels in each row. Modes 0/1 already
contain RGB and bypass the predictor. The predictor starts at zero at each block;
the first row is therefore copied exactly. There is no shared memory, subgroup
operation, atomic, scan or barrier within the shader, and no dependency between
workgroups. The longest serial loop is 128 rows.

The two eyes may choose different block sizes. Their checked metadata arrays are
independent; dispatch covers the maximum block count and excess groups return
before accessing the smaller eye's metadata. Within an admitted block, each
column/row is written exactly once. Complete CPU inspection proves block coverage,
row order and non-overlap. Image bytes and sampled values are independently checked
after every invocation, so holes, overlap mistakes or a missing predictor reset
cannot pass merely because the shader completed.

Admission, bounds and lifetime
Both immutable packets finish outer/header/table/block CRC, strict LZ4 grammar,
bounded decompression and dimensions before any mapped write, poison or submission.
Prepared state is invalidated at every admission attempt; upload requires a
successful complete admission for that same Session. Malformed right packets,
valid packets with wrong right-eye dimensions and upload after failed admission
must leave left mapped memory and submission count unchanged.

Metadata contains one block count followed by row, height, mode and raw RGB offset
for each block. Eight words per existing 16x16 tile provide bounded storage:
the number of blocks is at most ceil(height/16), which is no greater than tile
count, and one plus four words per block fits that allocation even for a 1x1 eye.
Residual input is padded with zero bytes through its final uint. Checked dimensions
cap raw RGB at 12 MiB, so the maximum byte address and metadata offsets fit uint32.
The shader independently checks the raw count, block domain, block height, mode
and row-derived offset before accessing pixels. Its final p+2 byte lies within
the validated block; the final packed uint remains inside padded allocation.

All three previously qualified image output arms are exercised: storage buffer
then device copy, direct UNORM storage with SRGB sampling view, and direct UINT
storage with SRGB sampling view. The shared image oracle requires actual device
limits, coherent host input and exact image-format/usage support. Optional format
lists are enabled only after querying advertisement. Every resource is poisoned
before reuse and retained through observed fences. Optimal-image RGBA is exact;
sampled float bits must equal the same-device buffer-copy baseline, with the
original half-code inverse-EOTF bound and exact alpha one.

Coverage
The default suite contains 460 fixtures: 114 previous generated/boundary images
times four asymmetric left/right block-size choices, plus four independent manual
RRB/LZ4 goldens. --full adds 48 fixtures at 576x640; --max-size adds eight maximum
2048x2048 constant/noise fixtures. The complete export adds 152 packet variants
from 38 images (24 captured eyes and 14 synthetic inputs, each at all four block
sizes), yielding 668 fixtures. Generated eyes differ and use unequal block sizes;
imported single-eye references are duplicated explicitly. Handwritten goldens
cover mixed raw/plain/up block modes, odd RGB tails and predictor reset boundaries.

The audit binds exact fixture identities, dimensions, both block-size/count/mode
domains, maximum dispatch coverage, raw upload bytes, aggregate mode counts,
all three output arms and every byte/color/admission/lifetime gate. It rejects a
same-count fabricated fixture set. Local imported pixels remain uncommitted;
receipts retain numeric evidence and source hashes only.

Observed qualification, 2026-10-09
Arc A770 and UHD 770 each passed the complete 668-fixture matrix in all three
output modes, with one warmup and one recorded invocation per mode: 8,016 exact
GPU invocations across the two devices. Each full matrix includes 512 generated
fixtures with differing left/right block sizes. Maximum inverse-EOTF error was
0.193304 code values on both devices; float equality remains same-device only.
Arc enabled queried format-list support; UHD used the ordinary mutable-view path.

The complete 668-fixture CPU/reference matrix passed ASan/UBSan. A separate Arc
ASan/UBSan GPU run passed all 460 default boundary/golden fixtures in all three
arms (2,760 invocations). No sanitizer diagnostics were emitted; leak detection
was disabled under ptrace. Five audit tests reject 42 negative mutations. Host
and Android API 29 ARM64 builds passed. All four SPIR-V modules pass spirv-val
for Vulkan 1.1 and declare only Shader capability. All 190 unique exported packet
and reference files match the committed 152-variant manifest. Numeric reports,
exact source/binary/shader hashes and scopes are in qualification-20261009.json.

Reproduce
  bash prototypes/vr444/experiments/rowblocks_gpu/build.sh
  python3 -m unittest discover -s prototypes/vr444/experiments/rowblocks_gpu -v
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/rowblocks_gpu/build/rowblocks-gpu-host \
    prototypes/vr444/experiments/rowblocks_gpu/build \
    prototypes/vr444/experiments/rgb_rowblocks/fixtures \
    --full --max-size --format-list --corpus EXPORTED_RRB_DIRECTORY > RESULT.json
  python3 prototypes/vr444/experiments/rowblocks_gpu/audit.py RESULT.json \
    prototypes/vr444/experiments/rgb_rowblocks/results/corpus-hashes.json
Use --device 1 for the second explicitly identified host GPU. Use --cpu-only with
the ASan executable for the full CPU/reference admission gate; omit full/max/corpus
for the 460-fixture instrumented GPU boundary matrix. Shader validation uses the
NDK's spirv-val by default, overridable with SPIRV_VAL. Android is compile-only.

Interpretation
This branch qualifies correctness and reuse. Its component clock fields are not
coordinated performance measurements and are not a complete client wall interval.
Concurrent correctness work and existing desktop GPU activity are allowed. A
later comparison must retain full CRC/grammar/decompression, upload, submission,
image readiness, actual wire sizes and the existing 895e9ef SIMD CPU baseline.
Independent blocks may improve parallelism but also increase wire and CPU costs;
no improvement is assumed from workgroup layout alone. Headset support, sub-15 ms
and optical freshness remain unevaluated. No Vulkan validation layer is enabled.

Independent compositor-team static review found no blocker in unequal-eye dispatch,
packed-byte tails, predictor reset, complete-pair admission or disjoint ownership.
