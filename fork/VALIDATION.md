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
