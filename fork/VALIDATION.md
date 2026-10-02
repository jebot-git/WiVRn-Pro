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
