Matched RRF1 / RRB1 stereo image-readiness comparison
===================================================

This is a host-only experiment. It does not launch, install, discover or query a
headset. It changes no production codec, transport, rendering or selection path.
RRF1 SIMD behavior is the previously qualified 895e9ef implementation; RRB1 is
the unchanged a7db779 independent row-block format. ff0c097 qualified the RRB
shader's three output modes, asymmetric eye partitions, maximum inputs and reuse.

Ten matched arms run in rotated order:
  RRF CPU SIMD restoration/expansion -> buffer-to-SRGB-image copy;
  RRF four-column prefix shader -> UNORM storage image with SRGB sampled view;
  RRB 16 / 32 / 64 / 128 rows, each with CPU SIMD copy and GPU direct-UNORM output.

The RRB shader has one independent block/column per lane. It resets its predictor
at each block, reads adjacent columns across lanes, and needs no subgroup or
shared prefix barriers. Each block's maximum serial depth is 128 rows. The RRF
shader has its existing segmented shared-memory prefix. Neither measured shader
is changed by this branch. GPU capability support is checked by the shared actual
Session constructor; this is not evidence of support on a headset.

Every run starts with immutable complete wire packets. CPU CRC, strict compressed
grammar validation, decompression and index construction occur inside the outer
clock for both eyes. CPU arms then restore rows and expand exact RGB to RGBA;
GPU arms upload checked RGB/residual plus their required metadata. All paths
include output poison, upload, command recording, readiness submission, fence wait
and timestamp retrieval. Two submissions are included (poison and readiness).
GPU timestamps alone omit material CPU and synchronization work. Reported pixel
upload bytes exclude metadata; the upload time includes it.

Poison is diagnostic overhead and its interval is separately reported. Subtracting
that interval does not undo cache effects or establish a production cost. The
whole outer wall is the primary measurement. Individual stage p95 values must not
be added. Image/session allocation and pipeline compilation occur before warmup.
Workspaces retain capacities; packet preflight runs on every iteration.

After each timed run, a separate observed submission reads every image byte and
every nearest-sampled SRGB texel. Both eyes must exactly match the independent
RGBA reference. Ideal EOTF/OETF roundtrip error must remain below half an 8-bit
code, alpha must be exact, and sampled float bits must equal the independently
verified first RRF CPU run on the same physical GPU. The oracle is outside the
readiness clock and is deliberately retained for every measured iteration.
Cross-driver float equality is not assumed.
The full oracle inserts CPU/GPU work and a gap between measured runs. This is
not a continuously rendered 90 Hz workload; its cache and frequency behavior
must not be assumed to match a native compositor.

Complete both-eye admission precedes any CPU prefix restoration, mapped write,
poison or submission. Each arm rejects a corrupt right eye and a well-formed
wrong-shaped right eye without changing a left mapped sentinel or submission
count. The next valid call fully overwrites previously restored CPU workspace
data. RRB upload requires a successful admission bound to that same Session.

Timing corpus: all 38 prepared RRF inputs and all 152 corresponding RRB variants,
with exact per-eye decoded-RGBA equality before pairing. Twelve actual stereo
pairs (24 captured eyes) and fourteen synthetic duplicated-eye profiles remain in
the batch; no noise, high-wire-cost or edge case is removed. Qualification adds
generated/boundary/maximum and independent RRF golden inputs, and reconstructs
RRB variants with deliberately different left/right block sizes. Independent RRB
manual goldens are covered in the parent ff0c097 qualification.

Commands (repository root; generated build/output directories are ignored):

  bash prototypes/vr444/experiments/rowblocks_image_timing/build.sh \
    validation/results/sub15-rowblocks-image-timing/build

  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    validation/results/sub15-rowblocks-image-timing/build/rowblocks-image-host \
    validation/results/sub15-rowblocks-image-timing/build \
    /tmp/sub15-rgb-rowfilter-gpu-corpus /tmp/sub15-rgb-rowblocks-gpu-corpus \
    --qualify prototypes/vr444/experiments/rgb_rowfilter/fixtures

The independent audit binds full manifest membership, all four RRB variants,
per-eye pixel hashes/dimensions/quality, actual wire lengths and row partitions,
every rotated arm/repetition, upload costs, submissions and exactness gates.
measure.py requires prior research-team idle coordination and snapshots both
corpora, both manifests, the executable, all eight SPIR-V modules and recursively
resolved local source dependencies before/after the batch. Desktop workload is
uncontrolled. Failed raw evidence is retained. Sanitizer/correctness clocks are
not performance evidence.

This endpoint excludes server reconstruction/encoding, wire transmission,
compositor availability/selection, XR submission and optical display latency.
RRB wire cost differs from RRF and is reported per source and partition. A better
client-only result would not establish a whole-pipeline improvement or sub-15 ms
readiness. Existing transport size caps still apply to every complete stereo
packet; large/noise packets require rejection or an explicit fallback.
