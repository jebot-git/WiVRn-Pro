RRF1 row-residual reconstruction into GPU images

This opt-in standalone prototype replaces the row restoration and RGB-to-RGBA
expansion after checked RRF1 decompression with a compute shader. It preserves
the exact existing decoded Q1/Q8 pixels. It does not change the wire format,
native client, compositor or renderer, and it does not access a headset.

The compression team's published RRF1 and SIMD branches are included unchanged:
  original 2773a29a179cbab87bbeb31c4a7303eb565a77fa -> local b06dc00
  original 895e9ef -> local d2256397
Their checked unpack_rgb API verifies the complete header/body CRC, dimensions,
strict LZ4 block grammar and bounded decompressed byte count. Mode 0 is raw RGB,
mode 1 is LZ4 RGB, and mode 2 is LZ4 of per-channel differences from the previous
row modulo 256. The first row of mode 2 contains ordinary RGB values.

Both immutable eye packets finish this CPU admission before any input mapping
is changed, image poison is submitted or decode work is scheduled. The shader
receives a complete RGB or residual buffer plus its checked mode and byte count.
It uploads three bytes per pixel, padded to a final uint; no CPU prefix restore
or RGBA reconstruction occurs in the GPU path. The CPU still performs CRC,
strict grammar checking, LZ4 decompression and the upload copy.

Three approaches within this branch
All shaders use 128 lanes. COLUMNS=1, 4 or 16 assigns that many complete columns
to one workgroup, with 128/COLUMNS contiguous row segments per column. Each lane
first sums its segment modulo 256. A shared-memory prefix scan finds the sum of
all preceding segments in the same column. The lane then rereads its segment,
adds each residual and writes its exact RGB output with alpha 255. This exchanges
only segment sums, requires no global scan or inter-workgroup synchronization,
and bounds each per-lane loop by the maximum admitted image height of 4096.
Raw/plain modes bypass the prefix scan and write RGB directly.

The two 128-element uvec4 arrays use 4096 bytes of shared memory. All 128 lanes
reach every scan barrier, including absent edge columns and empty row segments.
Column/row addresses are guarded before byte loads. Checked dimensions cap
width*height at 4,194,304, so all shader byte-address arithmetic fits uint32.
There are no subgroup, int8, int16 or optional atomic feature requirements.
The validated SPIR-V modules declare only the core Shader capability.

Each layout exercises three output arms: a storage buffer followed by a copy
into an optimal SRGB image, direct UNORM storage into a mutable image, and direct
UINT storage into a mutable image. The latter two expose SRGB sampling views.
This reuses the previously qualified direct_image_gpu oracle. Its only shared
source change is an optional, queried-limit-checked dispatch count; existing
callers retain their tile dispatch by default. The row-filter shader is dispatched
once per column group, including thin/wide shapes that need more groups than the
VR444 tile count.

Admission, color and lifetime gates
The private host device queries actual compute limits, memory flags, format
features and complete image-format properties before use. Hardware device types
are required. If --format-list is requested, VK_KHR_image_format_list must have
been queried and advertised before enabling it; missing/unqueried support fails.
No desktop capability is evidence of current headset support. Android builds
are compile-only. The shared oracle's conservative limits require 6144 bytes of
shared memory, more than this shader's actual 4096-byte use.

Every image and output buffer is poisoned before reuse, and sampled output is
poisoned with NaNs. Observed fences precede command/resource reuse. Image and
buffer barriers cover writes, copies, sampling and host inspection. The oracle
compares optimal-image bytes exactly to independent expected RGBA, including
alpha. Direct-arm sampled float bits must equal the same-device buffer-copy
baseline. The ideal inverse EOTF must recover the original byte within half a
code; alpha must be exactly one. No looser tolerance is assigned to a direct arm,
and bit equality between different drivers is not assumed.

For every fixture, a damaged right-eye packet must fail before submission and
before overwriting a sentinel in the left-eye mapped input. The standalone CPU
pass also requires failed decompression to clear its residual output. All three
RRF modes must appear. Four audit tests exercise 23 negative mutations, including
missing arms, forged color/admission gates, incorrect dispatch/upload counts,
nonfinite observations, unsupported device types and fabricated scope.

Coverage
The default suite has 118 fixtures: 84 generated distinct-eye cases across seven
shapes, three patterns, Q1/Q8 and plain/up encoder policies; 30 explicit boundary
shapes; and four independently hand-authored RRF/LZ4 goldens. Boundary heights
include 7/8/9, 31/32/33, 127/128/129, 255/256/257 and 4095/4096, with widths 1/17;
4096x1 and 4095x3 also exercise dispatch width and packed final-byte tails.
--full adds 12 cases at 576x640. --max-size adds constant and high-entropy
2048x2048 cases. The complete exported corpus adds 38 prepared images: 24 captured
eyes and 14 synthetic cases. Thus the full corpus suite has 170 fixtures.
Imported single-eye fixtures are duplicated into a pair; generated eyes differ.
The imported pixels remain local. Receipts contain only source hashes/numbers.

Observed qualification, 2026-10-09
Arc A770 passed all 170 fixtures in each of the three column layouts and each
of the three output modes: 1,530 measured stereo invocations plus 1,530 warmups.
Each layout covered 146 raw, 26 LZ4-plain and 168 LZ4-up eye packets. The integrated
UHD 770 passed the 118-fixture default suite for the four-column layout. Its
format-list option was disabled; the Arc runs explicitly enabled queried support.
These are same-device reference checks, not cross-device float comparisons.
The maximum inverse-EOTF error was 0.193304 code values on both tested devices.

The complete 170-fixture CPU pass succeeded with ASan/UBSan. All 76 local corpus
RRF/RGBA file hashes matched the compression team's committed export manifest.
Host and Android API 29 ARM64 warnings-as-errors builds passed; all 12 generated
SPIR-V modules passed spirv-val for Vulkan 1.1 and declare only Shader capability.
Four audit tests passed, and invalid column selection failed before Vulkan setup.
The original direct-image full 96-case suite passed after rebuilding against the
shared optional-dispatch change. Actual device Vulkan validation layers were not
enabled. Hashes, scopes and reports are in qualification-20261009.json.

Reproduce
  bash prototypes/vr444/experiments/rowfilter_gpu/build.sh
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    prototypes/vr444/experiments/rowfilter_gpu/build/rowfilter-gpu-host \
    prototypes/vr444/experiments/rowfilter_gpu/build \
    prototypes/vr444/experiments/rgb_rowfilter/fixtures \
    --columns 4 --format-list --full --max-size --corpus EXPORTED_RRF_DIRECTORY \
    > RESULT.json
  python3 prototypes/vr444/experiments/rowfilter_gpu/audit.py RESULT.json
  python3 -m unittest discover -s prototypes/vr444/experiments/rowfilter_gpu -v
Repeat the hardware command with --columns 1 and --columns 16. Use --cpu-only
for fixture/CPU admission without Vulkan initialization. Build outputs are local.

Interpretation
These runs establish byte, color, admission and reuse correctness. They are not
coordinated performance measurements: concurrent CPU research work is allowed,
and desktop GPU activity is uncontrolled. Component fields do not represent a
complete outer wall interval, and pipeline wall time overlaps GPU timestamps.
Poison, one-time setup and oracle operations have their own placement. No latency
benefit, headset sub-15 ms gate or optical freshness is claimed. A later fair
whole-path comparison must include checked CPU preprocessing, upload, submission,
image readiness and the current SIMD CPU baseline. RRF1's separate Q8 wire growth,
server preprocessing costs and transport size caps also remain relevant.

An independent compositor-team static review found no blocker in the segmented
prefix barriers, modulo arithmetic, packed tails or both-eye admission ordering.
Primary synchronization reference:
  https://docs.vulkan.org/guide/latest/synchronization_examples.html
