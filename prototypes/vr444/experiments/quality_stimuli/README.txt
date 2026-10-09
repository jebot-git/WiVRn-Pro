Application-source quality stimuli (explicit research profile)

This independent host OpenXR/Vulkan application submits three static patterns:
chroma-grid (1/2/4/8-pixel color alternation), rgb-ramp (independent channel
ramps), and fine-detail (horizontal/vertical/diagonal channel structure).
Every pattern has asymmetric eye/orientation borders. All source pixels have
opaque alpha and all three RGB channels at every pixel; no subsampling occurs
in the fixture generator. Right-eye content is spatially/channel distinct.

The source generation is DIRECT ENCODED-SRGB BYTE UPLOAD into actual application
RGBA8_SRGB swapchains, not a shader rasterization claim. After both acquired
images are waited, Vulkan writes both images, copies both actual images back,
and checks every byte against the original independent CPU fixture before
releasing either image. Every submitted frame repeats this check. First use of
each swapchain image saves its actual GPU readback separately. The app restores
COLOR_ATTACHMENT_OPTIMAL before xrReleaseSwapchainImage and uses the exact
OpenXR binding queue on one thread. Teardown destroys resources only after a
successful wait-idle or terminal device loss; an unknown completion terminates.

This exercises normal application submission, compositor/foveation, codec and
client paths when launched by the native runner. It DOES NOT inject pixels at
the server encoder. The original application source, the post-foveation server
RGBA, decoded codec RGBA, and final submitted headset image are four distinct
stages. Do not replace an upstream reference with decoded-client bytes. Q1
RRB32 exactness can compare post-foveation input to decoded RGB; an app-source
comparison must separately account for actual compositor/foveation sampling.
The upstream image is static per run, but source/frame joins still need the
actual app and native records; do not guess a time or geometry mapping.

Build (host only):
  ./build.sh /tmp/quality-stimuli-build
Requires a C++20 compiler, OpenXR loader headers/library, Vulkan, OpenSSL and
nlohmann/json. No external source tree is modified. `build.sh` creates binaries
and a build receipt. The receipt is a local source/command/binary binding, not
an assertion of a reproducible compiler or runtime environment.

Pure CPU source oracle (no runtime/device):
  QUALITY_STIMULI_BINARY=/tmp/quality-stimuli-build/quality-stimuli \
    python3 -m unittest discover -s THIS_DIRECTORY -p test_audit.py -v
  /tmp/quality-stimuli-build/quality-stimuli --emit-fixtures \
    --pattern rgb-ramp --width 576 --height 640 --output /tmp/new-source-fixture

The optional `test-transfer NEW_OUTPUT GPU_INDEX` binary creates only owned
host Vulkan images and calls the application's actual transfer function. It
never loads OpenXR or contacts a headset. Run only in a coordinator-approved
GPU correctness window. It covers seven bounded/odd shapes, three patterns,
image reuse with changed bytes, and deliberately mutated right-eye uploads.

Live launch preparation (does not launch anything):
  python3 plan.py --binary /tmp/quality-stimuli-build/quality-stimuli \
    --binary-sha256 SHA_FROM_BUILD_RECEIPT --bundle /tmp/new-quality-app-bundle \
    --capture /ABSOLUTE/NEW/RUN/app-source --pattern chroma-grid \
    --width 576 --height 640 --frames 900 --seconds 30

The coordinator can replace ONLY server configuration `application` with the
plan's exact argv while recording the plan, binary/build hashes, actual command
and before/after source snapshot. Use a separate geometry/quality run profile,
RRB32 Q1 with compression=none, exact paired publication, source_trace=true,
and final-image quality capture. The joint 262144-byte envelope cap remains;
over-cap source detail is an explicit rejected pair, never a silent downgrade.
Dimensions are explicit in the plan. The app can alternatively use runtime
recommended dimensions when both width/height are absent; it fails if a bound
is exceeded rather than rescaling. The plan always supplies dimensions.

The app's own bounds are <=4096 each axis, <=4 MP/eye, <=16 swapchain images/eye,
<=10800 frame attempts, <=120 seconds between calls, <=1-second swapchain/fence
waits, and <=2-second graceful exit handshake. A stuck runtime or driver call
can outlast an in-process deadline; plan.py always provides a separate timeout
with two-second kill grace. Any missing footer/readback/pair fails artifact
admission. A timeout is not a successful capture. `session_ended` is recorded
separately because a complete finite pixel capture does not imply the runtime
acknowledged session exit; the native cleanup gate must enforce restoration.

Actual source evidence:
  app.jsonl: immutable source metadata; state transitions; each successful
    xrEndFrame target, period, eye/image identity, pose and FOV; final counts.
  eye{0,1}.source.rgba: original CPU-generated application pixels.
  eyeE-imageI.readback.rgba: actual image readback on first use, independently
    exact-checked every subsequent submitted use before release.
  FOV radians/bits use the OpenXR struct order LEFT, RIGHT, UP, DOWN.
  research_quality.py uses LEFT, RIGHT, DOWN, UP: reorder indices [0,1,3,2].
  Pose orientation is XYZW. Float bit vectors preserve actual float values.
  `predicted_display_time` is XrTime; it is not a measured display/photon time.
  Source is VIEW-space/head-locked with actual located eye poses and FOVs.

`audit.py CAPTURE NEW_AUDIT.json` independently regenerates original app pixels,
checks both eye/source/readback byte sets and hashes, exact float-bit metadata,
prior actual-image captures for every submitted view, and complete footer
counts. It uses immutable byte snapshots with before/after checks and rejects
unknown artifacts, symlinks, malformed identities and nonfinite JSON values.
`--fixtures-only` checks pure CPU emission and makes no GPU/OpenXR claim.
The auditor emits no codec quality pass, no latency pass, and no display claim.

Primary API contract consulted:
https://raw.githubusercontent.com/KhronosGroup/OpenXR-Docs/main/specification/sources/chapters/extensions/khr/khr_vulkan_enable2.adoc
This defines runtime-compatible instance/device creation, binding queue
ownership, and COLOR_ATTACHMENT_OPTIMAL at wait/release boundaries.

Qualification is recorded in qualification.json; this milestone has no actual
OpenXR/headset fixture run yet. Its direct byte-upload source path is a first
structured corpus. A shader-rendered world-space scene, motion, gaze, natural
textures and a broader content corpus remain separate future coverage.
