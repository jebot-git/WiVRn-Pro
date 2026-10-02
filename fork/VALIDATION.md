# Offline validation — 2026-10-02

Validated on this branch without starting the WiVRn streaming runtime, connecting to a headset,
changing installed services, selecting an OpenXR runtime or accessing tracker hardware.

| Check | Result |
| --- | --- |
| Linux server and OpenXR runtime, QSV OFF | Built successfully with warnings treated as errors |
| Linux server and OpenXR runtime, QSV ON | Built successfully with warnings treated as errors |
| FFmpeg QSV configure probe | H.264/HEVC encoders and QSV device type present; no GPU opened |
| Quest Pro ARM64 debug APK | Offline Gradle rebuild passed with lobby capture, application-owned anchors and marker helper |
| Actual PC/ARM64 protocol constants | Match: `7227f7ce3e454c1d` |
| Application-owned spatial source | 114 checks passed: capture/late completion, reload retry after failed localization, preserved epoch, fresh-launch restore, focus/tracking loss, reference changes and publication throttling |
| Anchor manager | 69 checks passed |
| Guided capture | 706 checks passed |
| Spatial transform/multi-client IPC | 126 checks passed |
| Wire round trip/truncation/signedness | Passed; 30,617-byte snapshot, 258 spans |
| Arc/Quest Pro/Wi-Fi/codec policy | Compile-time cases and source merge guards passed |
| Unsupported CMake configurations | NVENC ON, x264 ON, SolarXR OFF, desktop client and all encoders OFF rejected |
| Upstream merge helper | 5 tests passed: isolated merge, preserved conflict, dirty source, wrong remote, current revision |
| Bundled native Shora | 98 backend tests, 9 skeletal-model tests, 6 relay tests and 2 skeletal-model doctests passed |
| Internal worker | Fake serial acquisition, skeleton, HMD anchoring/expiry, resets, reconnect, ownership, SIGTERM and saved-profile reload passed |
| C++ worker supervisor | Automatic crash restart, matching manifest environment and bounded clean stop passed |
| Qt6 dashboard | 13 tests passed; real worker calibration/tuning and attach-only close passed; offscreen streaming/skeleton views inspected |
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

Live HMD tests remain suspended until the user requests them. QSV hardware
encoding, latency, marker capture accuracy, Quest permissions/UX, Wi-Fi routing
on-device and real Shora tracking/reconnect behavior remain hardware-unverified.
The PCI policy includes known discrete Arc IDs; unknown/new IDs need review.
