Official AMD CAS FP32 standalone stereo-image feasibility

Build host and Android29 aarch64 (no install/device operation):
 prototypes/vr444/experiments/cas_gpu/build.sh
Run:
 cas_bench SHADER_DIR OUTPUT_IMAGE_DIR [DEVICE_INDEX=0] [REPETITIONS=7] [WIDTH=576] [HEIGHT=640]
Android binary has the same arguments. Coordinate GPU/CPU timing windows; only the
root agent operates ADB. This executable creates no OpenXR session or live stream.

Official unmodified ffx_a.h and ffx_cas.h are pinned to AMD standalone CAS commit
9fabcc9a2c45f958aff55ddfda337e74ef894b7f. vendor/provenance.json records URLs and
SHA256; original MIT LICENSE.txt and copyright notices retained. cas.comp is our
integration wrapper, not a replacement CAS algorithm. No external sample assets.

Color/layout contract
Input upload bytes are FP32 LINEAR RGB in[0,1], alpha separate. RGBA32F imageLoad
performs no implicit sRGB conversion. CasInput is identity; CasFilter produces
linear RGB; storage output is RGBA32F with no extra EOTF/OETF. gray128 fixture
explicitly decodes sRGB128 exactly once on CPU; PPM visualization explicitly encodes
linear output to sRGB once. Alpha is nearest selected from source and never sharpened.
HDR/PQ and premultiplied transparent-edge behavior are outside this first SDR test.

Both eyes have physically separate input/output images and descriptor sets. All
CasLoad reads clamp against that eye's dimensions. Cross-eye perturbation test
changes every right-eye texel and requires bit-exact unchanged left-eye output at
both scales. Test patterns otherwise have3pixel eye disparity where applicable.
This protects against atlas seam bleed; it is not subjective stereo acceptance.

Seven deterministic patterns: gray128, smoothed0.2/0.8edge, gradient,3pixel checker,
text-like strokes, fixed integer noise and primaries. Source576x640 by default;
output1.0x or1.5xlinear scale (area2.25<=official4.0limit). Three shaders: actual
bilinear bypass, official sharpen-only, official upscale+sharpen. CAS strengths
0,.25,.5,1; strength0 is minimum CAS, NOT bypass. All code runs FP32, default AMD
algorithm options (no packed FP16/better-diagonal variant). Workgroups8x8 chosen
for simple correctness/portability; not the upstream swizzled/unrolled optimized
sample dispatch. Thus these are integration costs for this wrapper, not optimal
vendor performance. Format/compute/timestamp limits are checked; CPU devices reject.

Per cell: one warmup then requested repeats; five mode orders rotate by pattern.
Two eye dispatches share one command buffer and timestamp bracket. GPU interval
excludes upload/readback; includes the two dispatches and timestamp/queue scheduling
span. Wall interval includes CPU input copy, command preparation, upload, execution,
fence wait and full float readback. Vulkan instance/images/pipelines persist across
patterns/modes for each scale; initialization is outside recorded timings.

Bilinear control and CPU reference share CasSetup FP32 scale/offset, GLSL precise
coordinate arithmetic and CPU -ffp-contract=off to avoid coordinate reassociation.
Validation checks: finite0..1RGB, preserved alpha, CPU bilinear match for bypass,
flat gray drift<=1e-3linear and unchanged displayed gray code, separate-eye invariance.
Quality diagnostics are linear MSE/max delta AGAINST BILINEAR (not a pristine scene),
left/right MSE, horizontal differences and edge overshoot above.8/undershoot below.2.
They quantify alteration/ringing, not visual benefit. Pattern-specific zero halo
says nothing about real game scenes or temporal codec noise. PPM captures atCAS.5
forgray/edge/text are display-encoded artifacts, not through-lens captures.

No network, codec, compositor, optical MTP, subjective acceptance or production
latency is measured. Full readback and float images intentionally establish a
reference path and exaggerate memory use relative to a future fused implementation.
Integrating after defoveation or into the live decoder requires a separate design,
final-image color validation, workload timing and wearer testing. Avoid stacking
CAS with Meta quality sharpening during the initial comparison.
