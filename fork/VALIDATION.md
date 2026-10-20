# Validation — 2026-10-02

## Earlier offline validation

Validated on this branch without starting the WiVRn streaming runtime, connecting to a headset,
changing installed services, selecting an OpenXR runtime or accessing tracker hardware.

| Check | Result |
| --- | --- |
| Linux server and OpenXR runtime, QSV OFF | Built successfully with warnings treated as errors |
| Linux server and OpenXR runtime, QSV ON | Built successfully with warnings treated as errors |
| FFmpeg QSV configure probe | H.264/HEVC encoders and QSV device type present; no GPU opened |
| Quest Pro ARM64 debug APK | Offline Gradle rebuild passed with lobby capture, application-owned anchors and marker helper |
| Actual PC/ARM64 protocol constants | Match: `7227f7ce3e454c1d` |
| Application-owned spatial source | 123 checks passed: capture/late completion, reload retry after failed localization, preserved epoch, fresh-launch restore, focus/tracking loss, reference changes and publication throttling |
| Anchor manager | 69 checks passed |
| Guided capture | 706 checks passed |
| Spatial transform/multi-client IPC | 126 checks passed |
| Wire round trip/truncation/signedness | Passed; 30,617-byte snapshot, 258 spans |
| Arc/Quest Pro/Wi-Fi/codec policy | Compile-time cases and source merge guards passed |
| Unsupported CMake configurations | NVENC ON, x264 ON, SolarXR OFF, desktop client and all encoders OFF rejected |
| Upstream merge helper | 5 tests passed: isolated merge, preserved conflict, dirty source, wrong remote, current revision |
| Bundled native Shora | 104 backend tests, 9 skeletal-model tests, 6 relay tests and 2 skeletal-model doctests passed |
| Internal worker | Fake serial acquisition, skeleton, HMD anchoring/expiry, resets, reconnect, ownership, SIGTERM and saved-profile reload passed |
| C++ worker supervisor | Automatic crash restart, matching manifest environment and bounded clean stop passed |
| Qt6 dashboard | 14 tests passed; real worker calibration/tuning and attach-only close passed; offscreen streaming/skeleton views inspected |
| Qt D-Bus | Isolated mock verifies status, paired keys, pairing, config write, stale-editor conflicts and WayVR status/restart |
| Configuration migration | No-overwrite precedence, private file permissions, malformed input and removed-feature handling passed |
| Current user profiles | Temporary copies of current WiVRn config/keys/cookie and Shora TOML imported; JSON round-trip and Rust parsing preserve settings; original files unchanged |
| Staged CMake installation | Combined executable, unified Qt dashboard, single service, spatial helper, third-party licenses and runtime libraries present |
| systemd unit verification | Unified unit passes `systemd-analyze --user verify` using staged executable paths |
| Bundled upstream WayVR | Release source build passed at `4590371a4c8f6a5b30e2e7a3fdb3b8af15b6d65e`; OpenXR/Wayland/X11, no OpenVR or Whisper |
| WayVR source audit | 723 source hashes and the three-file integration patch verified |
| WayVR supervisor | Fake worker crash/restart, normal exit, missing executable, parent-death signal setup, process group, manifest/environment binding and bounded stop passed |
| WayVR configuration | Upstream parser preserved every saved option from a temporary copy of the current profile (within f32 precision); original and copied files remain byte-identical |
| WayVR Rust tests | 3 passed: upstream language assets plus saved-profile loading; no XR/Vulkan initialization |
| Combined WayVR package | Latest QSV ON server/runtime built; staged source-built worker, libraries, OpenXR-only CLI, Qt modules, licenses and systemd unit verified |
| Monado private-socket patch | Reverse-apply check passes against the patched build source |

Build artifacts remain under ignored `build/`. The integrated packages are staged
under `build/package-integrated-off` and `build/package-integrated-on` for QSV OFF
and ON respectively, never into the live prefix. Older package directories predate
the full Shora integration. The final build is configured with QSV ON and the Qt6
dashboard enabled. The Quest Pro APK was rebuilt offline for the subsequent native
anchor/helper integration at `build/android/outputs/apk/debug/wivrn-native-debug.apk`.
The full `fork/test-offline.sh` passed again (18 Python tests plus native checks),
and the actual PC/ARM64 protocol constants still match. No installed service or
headset state was changed. UI rendering/interaction and reconnect behavior on the
Quest remain untested; the spatial lifetime tests use mocked OpenXR calls.

The latest WayVR integration is staged separately under `build/package-wayvr`.
The service was verified using a temporary unit pointing to the staged server.
Only the WayVR CLI `--help` was executed, before any XR initialization. Its seven
upstream unused-code warnings remain; the C++ host build passed with warnings as
errors. Missing libinput link metadata was supplied from extracted development
RPMs under `/tmp`; no system package was installed. Qt desktop controls were
inspected offscreen. WayVR rendering, portal capture, in-headset Qt interaction,
GPU performance and end-to-end service/headset reconnect remain untested.

The official upstream check found 53 commits after the imported v26.9 baseline
at fetched master `0d9e6e121a180f7dc9b806e9f04284f89bea6bb8`. These were reported,
not merged; synchronization tooling is ready for a reviewed future update.
GitHub workflows are supplied but have not been run on a hosted fork.

At that stage, live HMD tests were suspended until requested. QSV hardware
encoding, latency, marker capture accuracy, Quest permissions/UX, Wi-Fi routing
on-device and real Shora tracking/reconnect behavior remain hardware-unverified.
The PCI policy includes known discrete Arc IDs; unknown/new IDs need review.

## Initial anchor-assisted calibration protection

The QSV ON Linux server/runtime and Quest Pro APK build passed with the reference
guard and room-fixed calibration guide. Actual PC/ARM protocol constants remain
`7227f7ce3e454c1d`; this change adds no network packet fields. The full offline fork
suite passed (19 Python tests and the native checks), including 123 spatial-source
checks and a new calibration-spot geometry/persistence test. The Rust workspace
passed; the backend was also tested with the server's `face-xr` feature (104 tests).
Existing generated-code/dependency warnings remain; new ignored reset-result
warnings were fixed in the tests.

The new `spatial_tracking_smoke.py` ran the linked worker against a fake SEQPACKET
spatial publisher, fake serial PTY and HMD feeder. It verified initial settling,
explicit reset gating, generation changes, anchor movement, packet expiry,
HMD-feed loss and socket disconnect. Mounting reset alone did not resume learning.
Unit tests additionally cover session changes, reversed timestamps, replay,
duplicate anchors, tracking-only diagnostics, loss of anchor validation, frozen
drift offsets and discarding drift history across reference changes.

The existing worker/Qt smoke suite passed again, including calibration, settings,
full-profile persistence and reload. The Qt reference-state panel was inspected
offscreen, and its test checks disabled reset controls and countdown cancellation.
The Quest guide was tested with pure pose transforms and mocked XR lifecycle
calls; its persistence, hidden-on-loss behavior and default-off overlay were
verified. No deployment, installed service change or live HMD test was performed.
The new 5 cm / 3 degree / 2 second guard thresholds, calibration-guide usability,
and actual reduction in body-tracking drift remain hardware-unverified.

## Unified build and live deployment

The user resumed hardware testing and confirmed the headset and trackers were
ready. Built and installed the complete QSV-enabled server/runtime, bundled
SlimeVR-Rust/Shora backend, Qt6 frontend, upstream-source WayVR and wayvrctl,
and Quest Pro APK. PipeWire audio is enabled. Prefix:
`~/.local/opt/wivrn-pro`; Quest package: `org.meumeu.wivrn.spatial`.

The native WayVR battery integration adds three tests (six WayVR tests total):
formatting of low/unknown/offline values and overflow, stale-cache expiry, and
actual Unix-socket request/response framing. All passed. The source audit verifies
all 723 imported inputs and the updated integration patch. The offline fork suite
passed again with Unix-socket permission; its first sandboxed attempt could not
bind a test socket. The supervisor test checks the private Shora status socket.

Initial startup exposed a stale C++ object/header layout mismatch in a damaged
Ninja dependency database. Removed that generated database and rebuilt all host
objects; verified the supervisor headers now appear in main.cpp's dependencies.
Disabled unused C++ module scanning. A separate Quest startup failure submitted
an invalid OpenXR pose: lobby rendering now waits for valid located views, and UI
viewport/window poses start with identity orientation. The rebuilt client then
connected successfully; xrEndFrame failures now include layer-pose diagnostics.

Live observations after the fixes:

- Unified service remained active with zero automatic restarts during observation.
- Quest Pro paired over Wi-Fi; Arc A770 VAAPI HEVC streaming established.
- WayVR reached FOCUSED, launched the integrated Qt dashboard on its virtual
  Wayland display, and exposed dashboard/watch/desktop overlays through IPC.
  A second attach-only Qt frontend runs on the PC desktop.
- The native battery hook connected to the supervised Shora process: six active
  trackers, four real readings (chest/hip 90%, ankles 80%), two knee extensions
  without battery values. Unknown values remain unknown.
- PipeWire `wivrn.sink` exists and is RUNNING. Audible output and microphone
  capture were not verified by listening/recording.
- Native spatial publication reports a valid tracked HMD, saved room scene,
  boundary and the direct WiVRn runtime alignment. Shora receives the HMD and
  reports `tracking_only_no_anchor_validation`; no new application-owned anchors
  or body calibration were created by this launch test. Drift learning remains
  gated by `reference_needs_reset`, as intended.
- Old WiVRn, Shora server/proxy, spatial-bridge and shora-wayvr services are
  inactive; legacy Quest clients were force-stopped. They were not permanently
  disabled. The unified service is linked and started for testing, not enabled
  at login. Profiles were backed up under ignored `build/live-test-20261002`;
  existing WiVRn/Shora settings and WayVR profiles were retained.

Headset capture confirms the connected client and its in-headset settings UI.
WayVR battery-hook acquisition and Qt surface creation are verified in logs.
The user confirmed that WayVR, the integrated dashboard and wrist battery text
are visible and working. Individual dashboard controls were not exhaustively tested.
QSV was compiled but this carried-over configuration uses VAAPI, so QSV hardware
encoding was not exercised. Face tracking was inactive in the observed session.
Fresh marker capture, body reset and drift-accuracy testing remain separate
physical calibration steps. Runtime screenshots/logs and private room data are
not committed.

## QSV hardware session

The subsequent requested QSV session initially failed with `Cannot map Arc VA
surface for QSV: Invalid argument`. FFmpeg's VAAPI `map_from` handles DIRECT
before dispatch can reach QSV `map_to`, rejecting the differing hardware pixel
format. The mapping now uses READ for QSV, whose handler shares the VA surface
handle. DRM mapping retains DIRECT. Slot setup first recovers the actual VA
surface allocated by the DRM view, then creates its QSV view; previously QSV
referenced a different allocation than the one Vulkan filled. Mapping errors are
checked and color properties copied to the QSV view.

Reference: FFmpeg's [VAAPI mapping implementation](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0/libavutil/hwcontext_vaapi.c)
and [QSV mapping implementation](https://raw.githubusercontent.com/FFmpeg/FFmpeg/n8.0/libavutil/hwcontext_qsv.c).

Rebuilt and installed the host with warnings treated as errors. The live Arc
A770 session now initializes all three encoders as `qsv (h265 10-bit)`, with no
fallback. Quest stays connected, WayVR reaches FOCUSED, and the native Shora
battery reader reconnects to all six trackers. The user's saved 100 Mbps server
configuration remains unchanged; the Quest's existing 50 Mbps selection negotiates
24.6 Mbps per eye plus 0.6 Mbps for the third stream. Sustained latency/quality benchmarking was not
part of this launch test.

## Permanent installation and boot startup

Removed the seven superseded RPMs (`wivrn`, `wivrn-dashboard`, `slimevr`, and the
four `slimevr-server-rs` backend/Qt/WayVR/WiVRn packages), their installed service
units, the old WayVR AppImage and launcher/icons, and the standalone spatial
service. No dependency autoremove was used. Removed four legacy Quest packages
with `pm uninstall -k`, preserving application data; only
`org.meumeu.wivrn.spatial` remains installed. Source checkouts and user profiles
were retained. Private backups of profiles, service definitions and launcher
metadata are under ignored `build/install-migration-20261002-092203`.

Installed the current fork into `~/.local/opt/wivrn-pro`, registered its four CLI
launchers, desktop entry and icon, and enabled `wivrn-pro.service` plus
`wivrn-pro-dashboard.service`. User lingering is enabled. The core is independent
of the graphical session and starts at boot; the attach-only desktop dashboard
starts at graphical login. WayVR and its virtual dashboard follow the headset's
VR session. Desktop autologin and Quest boot behavior were not changed.

The lingering server now refreshes only the display/session variables from the
systemd user manager before spawning WayVR. A live boot-environment simulation
explicitly removed display variables from the server: `/proc` inspection showed
an empty display environment in the core and the correct Wayland/X11 environment
in WayVR, which reached FOCUSED. The temporary test override was then removed.
The supervisor tests, unit verification, configured desktop launcher validation,
and host rebuild passed. All six real trackers are active, QSV HEVC 10-bit remains
selected, both permanent units are enabled/active with zero restarts, and legacy
RPMs/services are absent. No actual reboot was performed.

## WayVR hand input from WiVRn

Adapted the local KWin gesture/ray algorithm and interaction design into the
source-built WayVR worker. The hand wrist attachment has its own pose and frame
conversion; saved offsets remain applied. Pointer cancellation releases the
original click target and any grabbed window, including on tracking/focus loss.
The server rejects hand samples older than 150 ms by production timestamp.

The release-mode WayVR suite passes all 14 tests, including pinch debounce and
hysteresis, ambiguity rejection, neutral rearming, tracking loss/reacquisition,
NaN rejection, estimated fingers, wrist/aim independence, lowered hands,
shoulder-ray geometry, smoothing and wrist-panel orientation. Existing battery
socket tests pass outside the filesystem sandbox. `fork/test-offline.sh` passes,
including source/patch auditing, policy, protocol, spatial and supervisor tests.
The host build and installation into `~/.local/opt/wivrn-pro` succeeded. Existing
upstream Rust dead-code warnings remain; no new dependency or Quest APK is needed.

Live startup with the Quest Pro confirms both OpenXR hand trackers initialize,
WayVR reaches FOCUSED and both pointers select hand-joint input. QSV HEVC 10-bit
remains active for all three streams; both installed services remain active with
zero restarts. Headset capture shows the running overlay session. The user subsequently reported unreliable middle-pinch grabs, a 90° clockwise
watch rotation, and that the cursor should follow the ray. These observations
led to the follow-up below.


### Grip, cursor and direct finger touch follow-up

Middle pinch now maps to the middle mouse button. Window movement uses a curled
hand grip with independent activation/release thresholds, wrist-relative motion,
and bounded finger-occlusion tolerance. An extended index used for pointing or
touching cannot start a grip. The wrist frame has a counterclockwise 90° local-Z
correction. Hand pointers bypass the desktop movement modifier and click-freeze
behavior, and desktop button-down is delivered after moving to the ray hit.

Watch and virtual-keyboard contacts project the index fingertip onto the existing
surface geometry. Front approach, press debounce, withdrawal hysteresis and
per-hand target ownership prevent repeated or stale presses. Changing targets,
losing tracking or leaving the surface releases the old click target; ray pinches
must return to neutral after touch. Near input works below the far-ray zone.

All 21 WayVR release tests pass, including physical-grip geometry, aim loss,
bounded finger occlusion, wrist loss, open-hand release, extended-index exclusion,
rotated/scaled touch projection, touch jitter, repeated taps and deep penetration.
The full host build and pinned-source/patch audit pass. The worker was installed
atomically and restarted via the existing supervisor without restarting WiVRn.
The user confirmed all revised interactions work in the live Quest Pro session:
watch orientation, cursor following the ray, closed-hand window grip and open-hand
drop, and direct index-finger taps on the watch and virtual keyboard. This is a
functional live check, not an extended latency or tracking-robustness benchmark.

### Offhand three-finger space drag

Added a thumb–index–middle chord on the watch hand (left by default), routed to
WayVR's existing OpenXR space-drag action. It follows the watch hand assignment,
uses wrist displacement throughout holding and release, preserves existing space
movement settings, and suppresses that hand's mouse/touch interaction while held.
Offhand single-pinches have a 120 ms assembly delay; the chord presses after 80 ms
and releases after either pinch opens for 60 ms. Neutral rearming prevents the
remaining single pinch from turning into a click. Full grip still moves windows.

All 25 WayVR release tests pass, including offhand-only activation, staggered
chord assembly, release without click leakage, hand-role changes, ordinary
single-pinches, fist priority, aim loss and tracking-loss rearming. The host build
and source/patch audit pass. Installed the worker atomically and restarted only
WayVR: the existing headset session remains connected, the worker reaches FOCUSED,
both hands initialize, and the Monado space-offset API is available. Live logs
show normal space-drag start and end events.

For visual validation, launched a temporary separate OpenXR application showing
a 65 cm checkerboard at a fixed stage pose, initially 1.4 m ahead at eye height.
This avoids WayVR's configured compensation of its own overlays during space
drag (`space_drag_affects_world=false`); the saved configuration was preserved.
Headset capture confirmed the reference was visible. The user confirmed it moves
relative to the real room during the chord and stops correctly on release.
The temporary reference application was stopped afterward; WiVRn-Pro remains
running. The temporary application and capture are not included in the fork.

### Mouse priority, hand joysticks and double-pinch reset

Physical mouse or touchpad motion, wheel input and held buttons suppress hand
ray/touch clicks and window manipulation. Cursor control returns after 1500 ms
of inactivity and all physical buttons are released; held hand pinches must
return to neutral before clicking again. Reading devices is passive, with no
new grabs. WayVR's own virtual mouse is excluded. If device monitoring is
unavailable, hand cursor input stays suppressed and the worker logs the reason.
The existing input-group access on this machine provides device access.

The built-in wrist has a **Sticks** toggle (off on worker startup). Hold a
thumbs-up pose for 150 ms to create that hand's wrist-relative touchpad below
the thumb. Thumb travel controls the corresponding standard Touch thumbstick
axes; moving the whole wrist carries the pad without deflecting the stick.
Opening two fingers, invalid tracking, loss of focus, disabling the toggle or
worker exit releases input. A 150 ms server lease bounds stale axes. Physical
controllers retain their input profile. Hand mode uses a private same-user
SEQPACKET endpoint and raw hand-profile acknowledgements to avoid switching
itself out of hand tracking when the runtime advertises virtual Touch sticks.
Custom watch XML remains untouched; it can add `::HandJoysticksToggle`.

Two short offhand thumb–index–middle pinches reset the playspace to the saved
centre using WayVR's existing reset action. Each tap may last up to 300 ms,
with up to 350 ms between the first release and second press. Movement over
3 cm invalidates the tap, so ordinary space dragging does not reset the stage.

All 30 WayVR release tests and the native joystick protocol/socket test pass,
covering mouse button/idle ownership, capture loss, pad activation and carrying,
axis bounds, both hands, opening, tracking loss, invalid samples, double taps,
server lease expiry, physical-controller exclusion and disconnect/disable.
The complete host build passes. New gesture ergonomics still need headset
confirmation; prior successful gesture tests are recorded separately above.

Installed to the existing WiVRn-Pro prefix and restarted the core service.
The Quest reconnected; `SessionRunning` is true. WayVR reached FOCUSED and
logged three readable passive pointer devices, both hand trackers and the
connected joystick channel. Existing monitor-capture tokens, keyboard and
OpenXR action configuration loaded, and Shora battery reports resumed. The
full offline suite also passed (19 Python tests plus native geometry, wire,
bridge, configuration and worker-supervisor tests).

The virtual thumb pads now tilt 60 degrees around their local X axis toward
the user's viewpoint at activation. The centre remains below the thumb along
the tilted normal, keeping initial axes neutral; the whole frame remains
wrist-relative. Both joystick regression tests pass and the release worker
build succeeds. Installed by restarting only WayVR.

Thumb-pad mounting now uses the index/middle knuckles with 15–25 mm clearance
above the fist, captured relative to the wrist. The accepted 60-degree tilt is
preserved. Neither thumb position at activation nor subsequent thumb travel
moves the pad centre. The projected thumb position drives the pad indicator
and, after entering the centre once, the standard joystick axes. A white
centre target indicates this initial arming step, avoiding immediate movement
from the fully extended activation pose. Opening or tracking loss resets it.
All three joystick regression tests pass, including both hands, full travel
in all four directions, mount independence from the activation thumb position,
whole-hand movement, and loss/release behaviour.

### Thumb-centred activation correction

The fixed knuckle mount proved too low in live use. Activation now places the
pad 12 mm below the observed thumb along the pad normal, then locks that frame
to the wrist. Subsequent thumb travel changes the axes without moving the pad.
The accepted 60-degree tilt and 12% deadzone remain unchanged. All three
joystick regression tests pass, including neutral spawn at different thumb
heights and full travel. The release worker was built, installed and restarted.
This new positioning still awaits user confirmation.

Following further live feedback, the pad's stored offset now attaches to a
rigid frame derived from the four curled fingers (MCP and PIP positions),
excluding both the thumb and the runtime wrist pose. It still centres below
the thumb once on activation. All four joystick tests pass: full signed travel,
neutral activation, fist translation/rotation, independence from wrist pose
changes, and opening/tracking-loss release. The tilt and deadzone are unchanged.

### Initial Mac desktop relay

Added a native macOS companion and a Qt receiver launched as a WayVR-managed
window from a new dashboard tab. Its host/account profile is separate from
WiVRn and Shora settings. The first transport is SSH-carried JPEG at up to
15 fps, with explicit-focus mouse/keyboard forwarding and bounded input leases.
It does not yet provide audio, clipboard, low-latency H.264, gamepad passthrough
or automatic routing of raw headset devices between computers.

Ten protocol/receiver tests and fourteen existing dashboard tests pass,
including real 1080p JPEG decode, framing limits, SSH target validation, focus
release, Tab forwarding, backpressure and stream-loss handling. CMake configure
and Dashboard installation pass. The dashboard panel was rendered and checked;
the WayVR source manifest and patch audit still pass.

Built and ad-hoc signed the native companion on the user's Apple M4 Mac mini,
macOS 26.6.1 (25G76), after Xcode setup. Installed it under the user's Applications
folder and launched it in the graphical session. The user granted permissions
and reported "Virtual desktop ready". System Profiler confirms exactly one
1920x1080 WiVRn-Pro virtual extended display alongside the original 2560x1080
physical main display, with mirroring off for both. The companion listens only
on 127.0.0.1:9758. An SSH receiver decoded a real 1920x1080 desktop frame, which
was visually inspected. The receiver was then launched inside the running
WayVR compositor; process and toplevel creation were verified. The existing
unified headset session remains active. Live pointer/keyboard ergonomics and
the latest fist-mounted thumbpad change await the user's test report.


### Physical mouse routing and measured thumb-pad reach

The user confirmed Mac video and hand input, but neither the host mouse nor
the headset-passthrough mouse reached the receiver. WayVR selected its internal
input focus without asking its evdev worker to capture devices. Window selection
now requests capture; selecting the Linux desktop, closing the focused window,
or Super+Z releases it. Focus loss clears Wayland keyboard focus and releases
keys/buttons. Capture waits for physical keys and buttons to be released, and
the existing five-second watchdog remains active. Both WiVRn virtual HID devices
and host devices use the same route; the mouse-priority arbitration is preserved.
The user subsequently reported Mac capture working as intended.

Added opt-in local thumb-pad diagnostics: a marker in the private runtime
folder enables 10 Hz samples for at most two minutes, capped at 8 MiB. Only
wrist-relative joint coordinates in head axes and pad values are written, with
0600 permissions; no room positions, images, audio or network upload. Two
user-requested captures were saved under the ignored build/diagnostics folder.
The first trace showed positive Y maxima of +0.41/+0.34 versus negative Y of
-0.99/-0.90. The second repeated the limited upward reach (about +0.1), while
finger-gun poses provided roughly 35–48 mm of projected vertical thumb travel.

An extended-index mode now waits for the thumb to lower from fully upright,
centres below it, and fixes that centre to the proximal index segment. The
supporting index has pose hysteresis; thumb/wrist changes do not move the mount.
The radius is 24% of palm width, limited to 14–20 mm (about 15 mm in this trace).
The accepted 60-degree tilt and 12% normalized deadzone are unchanged. The
original fist pose remains available. Replaying the second capture produces
approximately -0.97/+0.98 and -0.98/+0.96 vertical ranges in the index mode.
The live version still requires user confirmation of comfort and reach.


All 36 WayVR release tests pass with the recorded-motion replay enabled. The
final index-flex hysteresis additionally passes the six joystick tests and
increases captured index-mode samples to 179/229, with sideways ranges of
-0.97/+0.99 and -0.96/+1.00. The worker was built, installed and restarted;
Mac receiver restoration and the existing headset connection were verified.
The upstream source audit passes. Raw hand recordings remain outside version
control, and the opt-in capture marker was removed after measurement.

To repeat local diagnostics, touch
`$XDG_RUNTIME_DIR/wivrn-pro/thumbpad-diagnostics.enabled` while Sticks is enabled;
results append to `thumbpad-diagnostics.jsonl` in the same directory. The marker
expires after two minutes. Save/truncate prior samples before another run to
avoid the 8 MiB cap. Set `WIVRN_THUMBPAD_TRACE` to the saved JSONL path for the
optional joystick trace-replay test. No diagnostic capture is enabled by default.

### Fingertip alignment and visible deadzone

Index-mode pads now centre exactly on the tracked index fingertip and follow
the distal index segment. They no longer retain a thumb-derived hovering offset.
The existing size, 60-degree initial tilt and axis range remain unchanged; the
thumb must enter the centre before the pad arms. Loss of tracked fingertip or
distal-joint data releases input. Six joystick tests pass, covering fingertip
position and flex, thumb/wrist independence, neutral entry and tracking loss.
Recorded-pose replay confirms anchoring and bounded axes; those older sweeps
predate this surface position and do not establish its new live reach.

The rendered pad now includes a pale centre ring and shaded interior at the
12% radial deadzone. The shader receives the same DEADZONE constant used for
axis output, keeping the display and input boundary synchronized. Release
worker/shader compilation and the upstream-source audit pass.

### Independent Mac Desktop Relay application

Moved the Mac relay to apps/mac-desktop, with its own standalone CMake project,
Linux executable, desktop entry/icon, license and connection window. Building
it requires Python/PySide6 and SSH, not WiVRn/OpenXR or the server dependencies.
The default destination is a regular desktop window; WayVR is optional. Removed
the Mac tab and package files from the WiVRn dashboard. Existing saved host/user
settings migrate once to the independent application's profile without editing
the legacy profile. A local compatibility command forwards old launch paths to
the standalone app.

The Mac companion now supports --background and a per-user Aqua LaunchAgent,
starting at login and restarting on failure. An MDR menu provides settings,
retry and quit; closing settings does not stop the background service. The
installer preserves an existing app path and signing identifier; the token is
copied to the new independent Application Support directory. Native build and
LaunchAgent installation pass; running state, loopback-only listener and equal
old/new tokens were verified without exposing token contents. macOS requested
renewed screen-capture permission after this rebuild.

Fourteen standalone tests (including four launcher/migration tests), fourteen
existing dashboard tests and independent CMake/CTest configuration pass. The
standalone application and KDE entry were installed, and the normal desktop
launcher was started independently of WayVR.

### Large pad knuckle mount

When the index is curled, the large pad now centres on the index MCP knuckle,
using the fist frame for orientation. Thumb height no longer moves its mount.
The fingertip mount for an extended index, size, angle, neutral gate and visible
deadzone are unchanged. All six joystick tests and the release worker build
pass. Installed by restarting WayVR.

### Inside distal pad and Mac Relay menu icon

The large pad now centres on the inside surface of the curled index finger's
last segment, between the distal joint and tip, offset by the tracked finger
radius plus 2 mm. Its position follows that segment; its accepted angle, size,
neutral gate and 12% deadzone remain unchanged. Both pad sizes release input
when distal-index tracking is lost. Seven joystick tests pass, including
mirrored-hand placement, distal flex, thumb independence and tracking loss.
The release worker was built, installed and activated with a WayVR restart;
the WiVRn session remained running. These geometry checks do not establish
live comfort or reach.

Mac Desktop Relay now uses a light bitten-apple silhouette on a dark rounded
square. The SVG was rendered and inspected at 128, 64, 32 and 24 pixels, installed
in its independent application prefix, and KDE's menu cache was refreshed.

### Inside middle-segment pad

Moved the large pad to the inside of the index finger's middle (second)
segment, between PIP and DIP joints. Surface clearance uses those joints'
radii, and loss of their tracking releases input. The small fingertip pad,
angle, size and deadzone are unchanged. Seven joystick tests pass, including
middle-segment placement and movement on the synthetic curled-hand fixture.
The release worker built successfully and was installed with a WayVR restart;
the WiVRn session remains active. Live comfort still needs user confirmation.

### Photo-guided raised pad and stable palm mount

Using the user's 1.jpg/2.jpg references, the large pad now sits above the curled
index on the thumb side of the fist. Its nearest rim clears the finger by
2 mm; placement accounts for tracked finger radius and the tilted disc's
projected extent. The initial position is stored in a palm frame derived from
MCP/metacarpal positions, so finger curl and thumb motion no longer recalculate
its offset or orientation. It translates and rotates with the fist as requested.

Initial axes align to view yaw with head roll removed, retaining the 60-degree
tilt; subsequent head motion does not rotate them. Pad size, deadzone and neutral
entry are unchanged. Eight joystick tests pass, including mirrored placement,
finger-flex independence, rigid hand motion, head-motion independence and
tracking-loss release. Saved-motion replay also passes, but its older sweeps
cannot establish reach or comfort for the new placement. Reference photos
remain outside the repository.

Release build completed and was installed with a WayVR restart. The WiVRn
session remains running.

### Finger-gun thumbstick clicks

Removed the small index-mounted pads. With Sticks enabled, extending the index
and thumb while curling the other fingers now holds that hand's standard
thumbstick click after a 150 ms debounce. Index/thumb hysteresis tolerates small
pose changes; leaving the gesture releases the button. The gesture owns hand
input without drawing a pad or sending analogue axes. The large thumbs-up pad
keeps its position, size, angle, deadzone and neutral-entry behavior.

The authenticated local WVJ1 link adds left/right click flag bits 3/4 to its
existing 40-byte packet. The server maps them to THUMBSTICK_CLICK and touch,
while physical controllers retain priority. Disabled, disconnected, expired,
untracked and unfocused input releases via the existing reset/timeout paths.
Ten Rust joystick tests pass with saved-pose replay, including debounce, held
clicks, release, pad-to-click transition and packet flags. The native socket test
passes left/right clicks, zero analogue axes, disable, timeout, physical-hand
mask and disconnect checks. The WayVR upstream patch audit passes.

Server/runtime and release-worker builds completed. Installed the new server
and worker, restarted the unified service, and verified the headset session
reconnected. In-headset thumbstick-click behavior awaits a user check.

### Thumbstick-click visual feedback

The finger-gun button now has a fingertip circle using the existing cyan/left
and purple/right pad colours. It uses 48% opacity before the click registers
and while the index remains extended with a relaxed thumb, and 100% opacity
while the thumbstick button is held. Relaxing the thumb immediately releases
the button and restores translucency; opening the hand or losing tracking
removes the cue. Initial alignment and fingertip orientation follow the hand.

The visual-only circle has no axis cursor or deadzone. It is excluded from the
protocol's analogue-pad activity bits and sends zero axes. Eleven joystick
tests pass with recorded-motion replay, including matching visual/click states,
release to the translucent cue and reset. The existing large pads retain their
rendering and input behavior.

Release worker and shader compilation passed. Installed and restarted WayVR;
the headset session remains running.

### Open WiVRn menu with both hand stick clicks

The server now detects both current emulated thumbstick clicks and sends the
existing stream_tab_change(applications) control packet to the Quest client.
This connects the previously separate PC input and headset-menu paths without
changing the client or wire format. A held chord fires only once. Both clicks
must remain released for 300 ms of fresh, enabled hand input to rearm; disabled,
stale or unfocused input cannot rearm it. This prevents reopening the menu when
WayVR resumes and its 150 ms finger-gun debounce completes with hands still held.

The local socket test passes single-click exclusion, one-shot behavior, partial
release, focus loss, full release/rearm, physical-controller priority and stale
input. Existing thumbstick click delivery and opacity cues remain unchanged.

Server build passed. Installed and restarted the unified service, reconnected
the Quest client and verified SessionRunning=true. The live gesture subsequently
logged "Opening WiVRn menu from both emulated thumbstick clicks"; ClientTab
changed to applications and the client reported XR_SESSION_STATE_VISIBLE,
confirming the menu command reached and opened the headset UI.

### Application-only thumb contact clicks (menu shortcut removed)

Removed the two-click menu chord and all of its server state/tests at the user's
request: the Quest menu retained simulated-controller mode and could not be
closed with hands. Emulated clicks now only feed the running application; they
never send a stream-tab/menu command. The stuck client menu was closed remotely.

Extending the index now only displays its translucent circle. The thumb presses
it through actual sphere-to-disc contact, accounting for both radial bounds and
depth, using the tracked thumb radius. It reuses TouchPress from the watch and
keyboard: 25 ms contact debounce, 4 mm press clearance and 14 mm release
clearance. Appearing already in contact or recovering tracking requires a fresh
withdrawal/approach. Opacity uses the resulting button state; axes remain zero.

Thirty hand-input tests pass with saved-motion replay, including no click from
pose alone, contact/hold/release on both hands, outside-disc rejection, tracking
loss and contact-at-spawn/stale-input protection. The native application-click
socket test also passes. The old recording verifies state invariants, not live
comfort of thumb contact.

Server and release-worker builds passed using the existing local libinput
sysroot. Installed both and restarted the unified service; the Quest reconnected
with SessionRunning=true and ClientTab=hidden. Live contact feel remains to be
confirmed by the user.

### Stick-press pads removed; finger gun unbound

Removed the index-mounted button circles, opacity mode, thumb-contact handling
and WayVR stick-click output. Finger-gun/extended-index poses now release any
active analogue pad and remain unbound by the joystick feature, without claiming
hand-input ownership. Existing pointer/pinch interactions and the large
thumbs-up pads retain their behavior. WayVR sends only enabled/analogue-activity
flags; no emulated stick-click bits are produced.

Eight joystick tests pass with saved-motion replay, including no pad or joystick
ownership for either finger-gun hand, no stick binding on thumb contact, release
from a previously active large pad and preservation of the analogue-pad tests.
The upstream patch/hash audit passes.

Release worker/shader build passed and was installed. WayVR restarted with
the headset session still running.

### Initial native performance overlay (2026-10-02)

The wrist **Perf** button cycles off → wrist → floating → off. The native panel
is passive: no input focus, grabbing, mouse routing or game-input blocking. It
starts off each WayVR launch. Wrist placement follows the watch hand selected at
spawn and hides on lost tracking; floating placement stays in the playspace.

WiVRn publishes a versioned 5 Hz snapshot in the private runtime directory as
`wivrn-pro/performance.json` (0600, atomic rename, exclusive publisher lock).
Feedback only updates bounded caches. JSON and filesystem work run on a worker;
WayVR reads it on a separate sampler thread. Both caches expire after two seconds.
Host CPU/Intel GPU temperatures and used/total RAM refresh once per second.
The existing headset/controller and Shora battery hooks are reused.

The panel shows the busiest recent XR pacer by its session-local numeric ID,
including overlay sessions. Monado's factory provides no application name here;
it is deliberately not labelled as the active game. FPS uses smoothed GPU-done
intervals, CPU time is wake-to-delivery pacing and GPU time is delivery-to-GPU-done
wait. These are estimates, not instrumented CPU/GPU execution time. The bounded
30-second graph samples the completion interval at 5 Hz; red means more than 10%
over the pacer's frame budget. It is a trend, not a full per-frame spike trace.
History clears when the producer/session/pacer changes or data expires.

Streaming timing reports are cached separately by stage and stream so a newer
receive report cannot erase an older frame's decoder/display timing. The UI shows
worst latest encode, delivery, decode and encode-to-display measurements across
fresh streams. Delivery includes transport/queue time; encode-to-display is not
motion-to-photon latency. Extra displays count incremental times_displayed per
unique frame, independently for each tiled stream; they are not SteamVR
reprojection counts. Invalid/missing measurements display as unknown.

Native publisher tests cover repeated and reordered feedback, late stage reports,
invalid intervals, bounded history, file permissions, exclusive ownership and
cleanup. Rust tests cover validation/expiry, source selection, memory accounting,
unsafe files and parsing both the real panel and watch layouts without XR.
Native source integration remains recorded in the pinned WayVR patch/hash audit.

### Settings ownership and capture-focus acknowledgements (2026-10-02)

The Qt settings frontend now reserves `io.github.wivrn.ProDashboard` on the login
session bus before creating a window. KDE and WayVR therefore share one instance;
a second launch from the other display exits without creating a window or moving
the existing one. Same-display launches raise the existing window. The bus name
is released automatically on exit/crash. A migration check preserves an already
running pre-update frontend and its unsaved settings until the user closes it.

Native-window clicks intentionally route the physical keyboard/mouse to WayVR;
Super+Z returns them to Linux. The examined 17:46–19:46 CEST logs contain one
capture notification transition, at 18:11:06, followed by release at 18:11:17,
so repeated notifications could not be reproduced from those logs. The existing
notification remains limited to once per WayVR process. Fixed a related race:
late ordinary Grabbed/Released acknowledgements no longer overwrite a newer
requested focus state and cause redundant capture commands. Emergency/manual
release still overrides it and permits an explicit later click to rearm.

Isolated D-Bus tests exercise desktop-first/VR-first exclusion and crash recovery;
14 existing Qt settings regressions pass. Input tests cover stale acknowledgements,
manual release/rearm and held-key/button release before capture.

### Sharpening and USB investigation (2026-10-02)

No additional sharpening pass is needed to expose Meta's filters. The client
already offers Disabled/Normal/Quality in Settings → Post-processing → Sharpening,
using XR_FB_composition_layer_settings. Meta documents Normal as RCAS based and
Quality as Meta Quest Super Resolution since v55. The streamed projection uses an
RGBA sRGB swapchain, avoiding the documented YUV-layer limitation. Saved flags are
preserved, defaults are disabled, and this investigation did not change the APK
or the user's selected filter. A standalone FidelityFX CAS compute pass is feasible
after RGB reconstruction, but would add a mobile GPU pass and must avoid double
sharpening. Prefer evaluating the existing Quality setting first.
References: https://developers.meta.com/vr/blog/vr-image-quality-meta-quest-super-resolution/
and https://gpuopen.com/fidelityfx-cas/ .

The sampled two-hour kernel log (approximately 17:46–19:46 CEST) contains 1,126
`usb 1-5: clear tt ... error -71` reports. Hub 1-5 is a Genesys 05e3:0610 USB2 hub;
its three Shiftall 04da:3f18 receivers at 1-5.1/.2/.3 map to ttyACM0/1/3.
All three disconnected together at 18:08:03, 19:14:26–27 and 19:40:07. After the
last event they re-enumerated by 19:40:09 and GX6 reports resumed at 19:40:11.
A burst at 19:29:15 preceded two tracker expirations at 19:29:20 (five-second
watchdog). ttyACM2 is on the separate 1-10 path and is not implicated.

This strongly implicates the shared USB path, not a serial read-timeout setting;
the exact hub/cable/port/power/firmware cause remains unproven. Receiver runtime
PM is already `on`, all were active; the hub itself has `auto` and zero suspend
delay, which is a possible follow-up experiment, not proof of the cause. Shora's
100 ms serial read timeouts are ordinary polling and ignored; actual I/O failures
trigger its existing two-second reconnect loop. First isolate the hub/upstream
cable/host port before increasing timeouts. No USB resets, power changes or
tracker configuration changes were performed for this investigation.
USB error semantics: https://docs.kernel.org/driver-api/usb/error-codes.html .

Final verification: server/runtime and release WayVR builds passed; all 44 WayVR
unit tests, 14 Qt regression tests, isolated cross-display D-Bus tests and native
telemetry tests passed. Installed server and worker hashes match their build
outputs. The installed launcher correctly declined a duplicate while retaining
the existing legacy desktop window. WiVRn-Pro restarted at 19:51 CEST, is active
with zero unexpected restarts, and Shora resumed all four physical tracker feeds.
The Quest ADB connection is offline and SessionRunning=false: in-headset visual
placement, live timing accuracy and the reported popup recurrence remain pending.


## 2026-10-02 — compact wrist controls and graphical tracker batteries

Sticks and Perf now share one horizontal row. The Shora wrist text block is
replaced by WayVR's existing tracker SVG, tile border, typography and battery
palette. The dashboard/performance details retain their textual summaries.
Tiles deduplicate sensors by physical device MAC; synthesized HaritoraX knees
and invalid battery reports are omitted. Valid zero and 100% are displayed.
Inactive, paused or older-than-three-second samples show `--`; connection loss
retains device identities without retaining stale percentages. Layout rebuilds
are limited to membership changes, checked twice per second.

All 46 WayVR tests passed, including physical/virtual device filtering, shared
battery deduplication, stale/offline/paused handling, membership changes and
actual wrist XML instantiation with headset, both controller templates and four
tracker templates. Release build passed. No Quest application was switched or
controller interaction test requested during this work.

Controller battery feasibility: current WiVRn Android battery sampling reads
ACTION_BATTERY_CHANGED for the headset only. The wire battery packet updates
only wivrn_hmd; wivrn_controller does not expose Monado battery_status. WayVR
already knows how to query left/right Monado battery status and render its
controller_l.svg/controller_r.svg tiles.

The standard XR_EXT_interaction_profile_battery_state_display provides the
missing runtime API: chain XrBatteryStateDisplayEXT into
xrGetCurrentInteractionProfile for each /user/hand path and accept batteryLevel
only with VALID set. It would require optional client extension enablement,
per-hand transport, and controller battery callbacks with stale-state handling.
Reference: https://registry.khronos.org/OpenXR/specs/1.1/man/html/XrBatteryStateDisplayEXT.html .

However, the saved complete Quest extension enumeration from 2026-10-01
20:22:46 (/tmp/anchor-headset-start2.log) does not advertise this extension.
That run identifies Oculus runtime 209.68.0 (OpenXR API 1.1.58); today's read-only
ADB inspection reports installed systemdriver 209.0.0.68.430. Its runtime binary
contains private METAX battery symbols, but that does not establish a supported
public API. The Android runtime brokers are disabled. A current Android input
dump had no active Touch Pro devices, so Android InputDevice.getBatteryState
remains unverified as a fallback, including reliable left/right identification.
No fake controller percentage, private API call, or APK/protocol change was
added. Public OpenXR controller-battery support requires runtime exposure before
this path can be implemented and validated on this headset.

Installed worker SHA-256 matches the release binary:
`5a4b976f9b3de41b156959bbf1e258c6135fe03dd0bcc17432bb45da92c64b89`.
Only WayVR was restarted, at 20:19:57 CEST. It reached FOCUSED, enabled both
hand-joint sources, and connected to Shora with six trackers/four battery
reports. No panel parsing errors were logged. In-headset visual review remains
pending; no server or APK replacement was needed.


## 2026-10-02 — wrist icon grid

Sticks and Perf now use original monochrome SVG joystick and performance-graph
icons, sized 40×40 inside the existing rounded-square button style. They occupy
the right column of the existing control grid: keyboard/edit/sticks on top,
recenter/floor/performance below. Actions and tooltips retain their existing IDs
and behavior. Fork icons live outside the upstream import and are embedded by
the asset provider.

Existing native layout test passed with the battery templates instantiated;
release build and 723-file upstream patch audit passed. Installed worker hash
matches `2cafb4a4e04672e997d7a59135e806fb2bcaffe327aa9001f1db0b006aab8dd6`.
Only WayVR was reloaded; in-headset visual confirmation remains pending.

## DomeD desktop-layer minification — 2026-10-20

The prototype was built in an isolated worktree from archived `639120d1`.
Before deployment it was ported to the exact installed server commit `15a53b72`
(WiVRn 26.9.3-pyrowave.3); unrelated worktree changes were preserved. The installed
Monado layer shader matches the prototype baseline byte-for-byte. The Monado shader patch is pinned by the existing
`monado-rev`. Filtering is opt-in on the server with `WIVRN_LAYER_FILTER`.
No gaze, encoding, protocol, client, or projection-layer changes are required.

The actual shader smoke test passed on Intel Arc A770: original/off output is
byte-identical, magnification is unchanged, unsupported alpha modes and projection
output are unchanged, flipped stereo subimages do not bleed, transparent fixtures
retain alpha, and the minified one-pixel checker fixture has reduced error.
Three reversed-order rounds use five warmups and forty measured dispatches per
case. GPU timestamps cover shader execution and float SSBO writes, not transfers.
The test changes only the production shader's output image into an SSBO for
readback. Source/patch hashes and all rounds are recorded in the ignored report.

| 512×512 output, eight overlapping layers | Original median GPU ms | Balanced median GPU ms | Quality median GPU ms |
| --- | ---: | ---: | ---: |
| Quad | 0.076 | 0.283 | 0.277 |
| Cylinder | 0.088 | 0.273 | 0.264 |

These optimized-shader synthetic figures are medians of round medians, from a live GPU
with other applications active. They are not additive predictions for both eyes,
full headset resolution, or a particular DomeD layout. Magnification adds footprint
estimation cost despite unchanged output. Filtering remains off by default.

A separate read-only sixty-second live DomeD/WiVRn smoke sample observed a 72 Hz
target, 4,273 newly presented stream frames and 26 repeats per stream over the
sampled endpoints, roughly 1.2 ms median encoding, and approximately 23% of one CPU
core for dome-compositor, 9% for dome-xr and 24% for the runtime compositor process.
The live feed lacks a separate layer-squash GPU timer. Its sampled latency gauges
are not a per-frame latency distribution and do not measure photon latency.
The active runtime and headset were not restarted for baseline collection.

The Prototype QSV-enabled server and OpenXR runtime build passed with the new shader and
mode logging. The environment lacked a libinput development linker symlink; a
local build-directory symlink to the existing runtime library resolved linking
without installing system packages. Existing WayVR unused-code warnings remain.

The installed-base smoke also uses the same SPIRV-Tools optimization pass as the
production build. All pixel/compatibility checks pass after optimization. Shader
medians vary with the live workload and clocks; quality mode being slightly faster
in this probe is not evidence that its higher maximum sample budget is cheaper.

The final build based on the installed `15a53b72` server passed with QSV, PyroWave,
stock PipeWire audio, the existing hand/controller policy patches and production
SPIR-V optimization enabled. The production layer SPIR-V passed spirv-val.
No Android/client or wire-protocol changes are introduced.
