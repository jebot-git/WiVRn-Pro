# Hands Frame utility

Hands Frame is a native WayVR utility inspired by the interaction workflow in
[VRHandsFrame's manual](https://vrhandsframe.eurekaworks.jp/docs/manual/).
It uses WiVRn's local compositor and existing WayVR pointer/overlay system.
It captures the PC-composited scene; Quest passthrough and headset-private overlays
are outside this capture source.
It does not embed VRHandsFrame or connect to Eureka Works' account services.

## Controls

Enable **Watch → Utils → Hands Frame**. Begin with both grips released, bring
both controllers within 35 cm, squeeze both grips, then spread them diagonally
to define opposite frame corners. For tracked hands, curl the middle, ring and
little fingers and use the two index-finger bases as corners; the thumb and index
finger remain available for the confirmation pinch. Both inputs must be tracked.

- Left trigger/index pinch: cycle Photo → Text/translation → 2D code.
- Hold and release the left trigger/pinch for at least one second: toggle repeat.
- Tap/release the right trigger/index pinch: take a still PNG.
- Hold the right trigger/pinch for one second: start **silent VP9 WebM** recording;
  release it or the framing grip to stop. Recording has a ten-second ceiling,
  15 fps timeline and 1024-pixel maximum side. No audio device is opened.
- Tilt the right controller stick vertically to adjust frame size; tracked hands resize by moving their corners.
- Press both triggers/pinches together: start a three-second still-photo timer.
  The captured area stays fixed in world space while the timer runs. Hold one
  framing corner to fine-tune its position during the countdown.
- Release the framing grips, lose tracking/focus, or disable the utility to cancel.
  A held gesture cannot reactivate framing until both grips return to neutral.

The native result panel supports Save, Copy, Open, Close, original/translated
text, paging, language selection, automatic 2D scanning and bookmarks. Repeat
captures share the bounded gallery rather than spawning a screenful of panels.
The ordinary WayVR grip/resize behavior works with controllers and tracked hands.
Bookmarks expose a small wrist panel; selecting it recalls the bookmarked result
in front of the user. Bookmarks are session-local, as in a temporary panel shelf.

Automatic scanning checks an active frame at most twice per second. A recognized
code closes framing and presents its contents; links open **only** after choosing
Open. HTTP/HTTPS are accepted as web links; arbitrary URI handlers are not invoked
from scanned text. Copy uses the local Wayland clipboard. Open also plays a saved
WebM with the desktop's configured player. The vendor-specific Steam-friend/PIN
sharing, social uploads, AI-comment accounts and Workshop integrations are not
reproduced: this implementation keeps capture and recognition local.

## Setup

The worker needs Python/Pillow, FFmpeg built with `libvpx-vp9`, and optionally
`wl-copy` for clipboard actions. Run `fork/setup-hands-frame.sh` to install the
capture/scanning Python libraries into the existing Spatial Subtitles environment.
This installs **no model weights**. The utility automatically selects that local
environment; `WIVRN_HANDS_FRAME_PYTHON` can select another interpreter.

OCR/translation reuse Spatial Subtitles' installed local models and device
settings. Install/configure those models using its existing setup workflow first.
Missing models or dependencies are shown as errors in the utility; there is no
implicit download, microphone capture or cloud fallback. `zxing-cpp` supports QR,
Data Matrix and other 2D/barcodes; an existing OpenCV installation can provide a
QR-only fallback. Target language cycles through the locally supported languages.

Still images are bounded to 2048 pixels per side. Unsaved captures live in the
private runtime directory, and Save copies them into
`~/Pictures/WiVRn-Pro/Hands Frame/YYYY-MM/`. Names are unique, saved files are
private and existing files are never overwritten. There are at most sixteen
result panels and two pending recognition jobs; busy encoders drop image samples
while preserving the recording's time base. Encoding has a 64 MiB output limit.

## Capture and input integration

WayVR's worker renews a short monotonic lease only while framing, recording or
finishing a requested capture. With no lease, the normal compositor fast path
remains available. While capture is leased, WiVRn composes the layers into its
existing RGBA target, copies the left eye before foveation and publishes a
versioned private CPU frame. That includes the scene and other submitted overlays;
the utility's own guide/results/wrist shelf are hidden for the capture.

One staging allocation is in flight at a time. A timeline semaphore determines
when its GPU transfer is complete; a worker performs all file publication. The
render path does not wait for the GPU or write frame files. A slow reader/encoder
causes capture drops, never an unbounded queue. Readback errors disable capture
until the lease is released. Native projection uses the located left-eye view
and the captured image's asymmetric FOV. No desktop screenshots or application
launch-layer changes are required for the integrated WiVRn compositor.

The CPU transport is `hands-frame.rgba` in the owner-only `wivrn-pro` runtime
directory: `WVRHF001`, little-endian width/height, monotonic capture nanoseconds,
four left-eye FOV angles and tightly packed RGBA. Nonblocking file locks prevent
torn reads. Readers validate ownership, permissions, dimensions, byte length,
finite projection, and a 250 ms age limit. Capture files are removed on normal
runtime shutdown; stale files cannot be consumed after a crash.

A recognized frame owns both pointers. Existing overlay grabs, watch groups,
keyboard input and virtual D-pads prevent framing activation. While framing,
WayVR releases its ordinary pointer actions and requests application input
blocking. Hand-as-controller pinch/squeeze emulation and walking input are also
reserved for the utility. They resume through their existing neutral/step rearming
rules, and hand skeletal pose delivery is retained. Tracking/focus discontinuities
cancel framing; they never trigger a confirmation on re-entry.

## Validation

The tests cover framing/rearming, still-versus-hold recording, repeat, timer,
tracked-hand curl/pinch hysteresis, native panel parsing, crop projection,
malformed/stale frame rejection, bookmarking/paging, and a real FFmpeg encode
verified with `ffprobe` to contain **one VP9 video stream and no audio stream**.
`fork/tests/hands_frame_readback_test.cpp` renders a known color into a two-layer
Vulkan image, exercises the production timeline/readback/file path using a
software Vulkan driver, and verifies pixels, lease expiry and cleanup.

These checks do not run a live headset or install the changes. Live crop alignment,
hand comfort, game compatibility and frame-capture overhead remain headset/GPU
validation items. WebM playback and clipboard access depend on desktop handlers.
