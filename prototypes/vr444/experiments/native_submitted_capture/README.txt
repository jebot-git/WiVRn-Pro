Bounded native submitted-image acquisition (research diagnostics only)

Default off, under WIVRN_CODEC_TESTS. No image-quality verdict, displayed-pixel
claim, independent reference, latency gate or promotion follows from capture.
Root still must build and execute the final codec/GPU/teardown/capture union.

Control in the application's config root:
  codec-test-quality-capture.json
  {"enabled":true,"max_pairs":6,"frame_stride":90}
Missing means off. enabled must be bool; max_pairs integer1..8(default6), stride
integer1..900(default90). Unknown/duplicate/invalid typed fields reject, even
with enabledfalse. Config is capped1024B. Enabled mode requires a NEW absent
codec-test/quality directory; existing evidence is never overwritten.

Eligible coherent stereo frames must have absolute frame ID %frame_stride==0,
matching the server raw-input capture grid when stride is a multiple of90. The
first such pair consumes an attempt, then IDs must advance at least the stride. At mostmax_pairs attempts can allocate/copy;
failed xrEndFrame still consumes its attempt and cannot publish a pair marker.
Current formats are actual R8G8B8A8_SRGB or B8G8R8A8_SRGB, copied unchanged with
RGBA/BGRA channel order recorded. Each eye is at most4Mi pixels and4096 per axis:
16MiB readback per eye,32MiB current pair,256MiB maximum raw disk payload for8.
This bound is separate from decoder workspace and XR image allocation memory.

Enabled stream swapchains request XR_SWAPCHAIN_USAGE_TRANSFER_SRC_BIT, verify
Vulkan format transfer-source support, and reject XR creation/capability errors.
Other swapchain callers retain the zero-extra-usage default. An acquired image
must successfully wait. After actual defoveation into its two array layers,
COLOR_ATTACHMENT_WRITE -> TRANSFER_READ barriers change to TRANSFER_SRC_OPTIMAL;
each submitted viewport rectangle copies to a disjoint coherent host buffer.
TRANSFER_WRITE -> HOST_READ barriers precede an observed stream fence, and the
image returns to COLOR_ATTACHMENT_OPTIMAL before XR release. The fence is waited
while the image is still acquired. The external acquisition watchdog bounds a
broken driver; in-flight allocations are never freed merely on a finite timeout.
Unknown queue completion keeps the buffers until the terminal queue drain.

Only successful xrEndFrame with the stream projection layer can expose files.
Raw and per-eye metadata use O_EXCL and fsync; a final capture-N-pair.json marker
is written after both eyes. Any write failure throws and leaves incomplete files
for audit; collectors must still require every artifact/hash, not marker alone.
A bad pose/submission leaves no completed pair. Capture is the stream projection
image after defoveation/color/dimming/cutout and before runtime warp/filtering or
other composition layers. Runtime filter hints do not prove their actual effect.

Per pair:
  capture-N-eye-0.raw, capture-N-eye-1.raw
  capture-N-eye-0.json, capture-N-eye-1.json
  capture-N-pair.json
The per-eye schema1 metadata includes source_boundary=submitted-image, frame,
warp, eye, decoder_generation_first_frame, dimensions/stride/order/actualformat,
subimage rectangle/arraylayer, successful_submit_xr_ns, readback-fence proof,
predicted target, render and encoded-input FOV/poses separately, unwarped_fov
[left,right,down,up]radians, foveationx/y, fullRGB/alpha input rects and input
layout, alpha image/sampler presence, outputalpha, masks, scale/bias, actual
shader alpha specialization and HMD SRGB conversion conjunction, and submitted
filter flags. Raw test codec inputs identify the fixed source-bound SRGB view
and sampler contract; arbitrary legacy decoder sampler/view formats are unknown.
Sampler state is not queried from Vulkan. Root must bind the actual built
shader bytes and decoder/defoveator sources from the APK/build receipt.

The initial ray mapping admits equal, unclamped actual viewports only; unequal
or clipped viewports reject rather than silently describing them as a full FOV.
Capture alone cannot manufacture the required independent same-render rawRGBA
reference. A later comparator/replay must use independently supplied source
pixels, the same actual mapping/shaders/color state, complete stereo identity
and a separate visible-headset assessment. The central10degree ROI is a later
comparison policy, not a crop or an assertion made by this capture writer.

Offline checks in validation-20261009.json:38 ASan/UBSan config/type/duplicate/
size/bounds/overwrite rejections;24 bounded frame-budget configurations; actual
Android stream.cpp and xr/swapchain.cpp syntax with diagnostics bothON andOFF;
source ordering guards. No Vulkan image copy or XR submission executed here.
overlay-parity-20261009.json verifies73-file before/after and bidirectional patch
identity at this standalone capture milestone; root later reconciles its union.
GPU-team independent source review checked acquired-image/barrier/fence ordering
and requested the added exact shader/input metadata for future reference replay.

Reproduce (read-only VFS, use a fresh output path):
  python3 prototypes/vr444/experiments/native_submitted_capture/validate.py \
    --native-root /path/to/unchanged/native \
    --compile-commands /path/to/Android/compile_commands.json \
    --simdjson-root /path/to/native/build/android-deps/simdjson-src \
    --out /tmp/submitted-capture-validation.json

Primary API contracts consulted:
  https://docs.vulkan.org/refpages/latest/refpages/source/vkCmdCopyImageToBuffer.html
  https://registry.khronos.org/OpenXR/specs/1.1/man/html/XrSwapchainCreateInfo.html
  https://registry.khronos.org/OpenXR/specs/1.1/man/html/xrReleaseSwapchainImage.html

Alignment follow-up: absolute modulo sampling avoids permanently offsetting the
client capture grid from the server frame%90 original-input readbacks. Root uses
stride90..900 in multiples of90 for same-reference acquisitions; selectors can
still skip eligible frames, so missing source/pair evidence must fail audit.
