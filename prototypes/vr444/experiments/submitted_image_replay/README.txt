Private submitted-image replay renderer
======================================
This is a bounded Vulkan graphics reference for the research quality path. It
has no OpenXR, networking, ADB or installed-application interfaces. It enumerates
local Vulkan physical devices solely to select the explicit GPU index.
It uploads independently acquired, opaque, pre-codec post-host-foveation RGBA
source pixels and uses the native reprojection shaders to render two private
SRGB array layers. Readback produces the actual output channel codes. A separate
capture adapter must bind the source identity and bytes to the exact native
submitted frame, and compare the resulting image to that actual capture.

CLI (one stereo job, external watchdog required):
  replay JOB VERT_BINARY_SPV FRAG_BINARY_SPV OUTPUT_PREFIX DEVICE_INDEX
  replay JOB --cpu-only
The diagnostic --omit-right switch deliberately leaves right readback poisoned;
it is exclusively an oracle negative control. Outputs are PREFIX-eye-0.raw and
PREFIX-eye-1.raw, in the actual output Vulkan format (RGBA or BGRA). The JSON
stdout contains actual GPU identity and explicit false OpenXR/quality/performance
claims. Output files must not exist. No timings or latencies are measured.

RLP1 job grammar, little-endian, complete both-eye admission before GPU work:
  u32 magic=0x31504c52, version=1, output_width, output_height, VkFormat, eyes=2
  then for eye0 followed by eye1:
    u32 input_width,input_height,nx,ny,alpha_specialization=0,srgb_decode=0
    112-byte push constants: 4xi32 rgb_rect; 4xi32 a_rect; 4xf32 scale;
                            4xf32 bias; 12xf32 cutout mask, row major
    nx u32 foveation_x lengths, ny u32 foveation_y lengths
    exactly input_width*input_height*4 RGBA source bytes (alpha255)
No padding or trailing bytes. Total input <=40MiB. Each image axis1..4096 and
area<=4MP. Foveation vectors must be odd-length1..129, segment values0..4096,
unweighted sums equal actual input extent and native weighted sums equal output
extent. Both outputs share dimensions. Full rgb_rect [0,0,w,h] only; alpha image,
unknown sampler/view, shader-side SRGB decode and nonopaque input are unsupported.
All float constants must be finite. Failure is unsupported/error, never pass.

The source-bound capture adapter is owned by the compositor team; it must use
independent host source .rgba, never codec-decoded/reference.rgba or client image
readback as the uncompressed reference. It must bind exact packaged vertex and
fragment binary SPIR-V. Native .spv files may be ASCII uint32 initializer text:
use verified selected .spv-opt/.spv-nopt binary or checked little-endian conversion.
reprojection.glsl here is copied from the existing native source for synthetic
qualification only. Its compilation by glslc is NOT asserted to be the packaged
application's optimized glslang/spirv-opt output.

The pipeline matches native RGB-only defoveation: triangle strip geometry,
position R32G32_SFLOAT and UV R32G32_UINT, native112-byte push constants, alpha=0
and do_srgb=false specialization, immutable LINEAR/NEAREST-mip/clamp sampler,
SRGB input view, SRGB color attachment, no blending/depth/culling and one sample.
Private targets have two array layers and per-layer2D-array views. The copyout
barrier orders color writes before transfer and transfer writes before host read;
both readbacks are exposed only after the actual fence. GPU resources outlive
submission, and terminal device wait accepts only success/device-lost. Software
ICDs are rejected. Actual image usage/format/dimensions and GPU limits are queried.

Geometry reconstruction is tested against the exact source-extracted native
loop in stream_defoveator.cpp. That reference compiles with native-style
-ffast-math; replay compiles without it. The host test compares every vertex byte
for2000 eyes. It does not prove different architectures/compilers generate the
same bits. Actual captured-metadata replay agreement is required before assigning
compression error to a mismatch. If it fails, capture exact prepared native VBO
and push-constant bytes in a subsequent version before assuming codec error.

qualify.py builds host/ASan/Android and validates both synthetic SPIR-V modules.
It checks 18 malformed jobs under ASan/UBSan, full stereo identity/BGRA rendering,
spatial eye distinction, repeat byte equality, right-eye omission, and a GPU ASan
run. Foveated and scale/bias CPU ideal checks explicitly allow at most1 code due
to hardware SRGB/filter approximation; the same-device reference comparison to
actual native captures remains exact, with no relaxed compression tolerance.
The final report's headset_used=false and native_renderer_executed=false remain
true scope limitations. Root alone executes Android replay and native capture.

Full central10-degree quality requires source/geometry/chroma provenance and the
root's conservative cone comparator. Even exact submitted-image agreement does
not prove original application equality or displayed photons; source-to-host and
visible-artifact stages remain independent gates.

Bounded actual-headset collection (root executes the unchanged runner only):
  python3 capture.py prepare --job-receipt JOB_ADAPTER/audit.json \
    --job-receipt-sha256 SHA --native-receipt NATIVE_BUILD_RECEIPT \
    --native-receipt-sha256 SHA --bundle /tmp/FRESH_REPLAY_BUNDLE \
    --adb-target TARGET --serial SERIAL
  python3 ../headset_review_smoke/run.py /tmp/FRESH_REPLAY_BUNDLE/plan.json \
    --out /tmp/FRESH_DEVICE_CAPTURE
  python3 capture.py audit /tmp/FRESH_DEVICE_CAPTURE \
    --plan /tmp/FRESH_REPLAY_BUNDLE/plan.json \
    --runner ../headset_review_smoke/run.py --output /tmp/FRESH_REPLAY_AUDIT

capture.py itself never invokes ADB or GPU commands. Preparation runs only the
already-qualified host --cpu-only parser on an immutable snapshot. It requires
the source adapter's build receipt, exact native shader bytes and headset serial
to agree, independently checks selected optimized/nonoptimized binary SPIR-V
against its actual embedded ASCII initializer, and snapshots four upload files.
The standalone Android executable/source qualification stays pinned at508c290b;
this is not a fresh Android build or a native-application ownership test.

The fixed acquisition is one stereo render plus one right-eye omission render,
each under a60-second device watchdog and75-second host command bound. It also
runs the device's CPU parser and collects exact two-eye hashes/raw bytes. Raw
files use base64 transport because the unchanged runner decodes stdout as UTF-8.
All ten planned commands, four uploads,26 total acquisition commands and53 raw
capture/receipt files are mandatory. The audit joins exact argv, bounds, returns,
deployed digest/path pairs, hardware identity and verified owned-directory
cleanup. It parses the captured hashed byte buffers and rechecks the complete
capture inventory, not a second unbound view of each result. A normal right-eye
image must differ from its actual-device0x5a omission sentinel, while the left
hash must remain identical. The two retained outputs keep their actual RGBA/BGRA
ordering. Device output and native submitted-capture comparison are separate
gates; the collection receipt always leaves quality_qualified=false.

test_capture.py uses simulated acquisition logs and the pinned local binaries,
native build receipt and shader files. Its pass is a fresh planner/auditor test,
not a new device result. Negative controls cover grammar, source/build/headset
splices, uploaded shader mismatch, malformed outputs, missing/reordered/failed
commands, host timeout, software GPU identity, symlinks, source/binary controls,
changed snapshots and an overwrite/restore capture race.
