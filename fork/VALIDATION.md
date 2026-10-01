# Offline validation — 2026-10-02

Validated on this branch without starting the fork server, connecting to a headset,
changing installed services, selecting an OpenXR runtime or accessing tracker hardware.

| Check | Result |
| --- | --- |
| Linux server and OpenXR runtime, QSV OFF | Built successfully with warnings treated as errors |
| Linux server and OpenXR runtime, QSV ON | Built successfully with warnings treated as errors |
| FFmpeg QSV configure probe | H.264/HEVC encoders and QSV device type present; no GPU opened |
| Quest Pro ARM64 debug APK | Gradle build successful, including final Wi-Fi route and decoder restrictions |
| Actual PC/ARM64 protocol constants | Match: `7227f7ce3e454c1d` |
| Anchor manager | 69 checks passed |
| Guided capture | 706 checks passed |
| Spatial transform/multi-client IPC | 126 checks passed |
| Wire round trip/truncation/signedness | Passed; 30,617-byte snapshot, 258 spans |
| Arc/Quest Pro/Wi-Fi/codec policy | Compile-time cases and source merge guards passed |
| Unsupported CMake configurations | NVENC ON, x264 ON, SolarXR OFF, desktop client and all encoders OFF rejected |
| Upstream merge helper | 5 tests passed: isolated merge, preserved conflict, dirty source, wrong remote, current revision |
| Service readiness helper | 2 tests passed: ready listener and bounded missing-listener failure |
| Embedded Shora | 6 tests passed, including synthetic Unix-socket backend restart and endpoint cleanup |
| Staged CMake installation | Executable, services, helper, licenses and both manifest libraries present |
| systemd unit verification | Both units pass `systemd-analyze --user verify` using staged executable paths |
| Monado private-socket patch | Reverse-apply check passes against the patched build source |
| Optional dashboard | Configure blocked: Qt6 development package unavailable locally; a dedicated CI job is supplied |

Build artifacts remain under ignored `build/`. Installation was staged under
`build/package`, not into the live prefix. The two staged packages are `build/package` (QSV OFF) and
`build/package-qsv` (QSV ON); the final build directory is configured with QSV ON. An explicit install
should use the desired final encoder configuration. The APK is debug-signed.

The official upstream check found 53 commits after the imported v26.9 baseline
at fetched master `0d9e6e121a180f7dc9b806e9f04284f89bea6bb8`. These were reported,
not merged; synchronization tooling is ready for a reviewed future update.
GitHub workflows are supplied but have not been run on a hosted fork.

Live HMD tests remain suspended until the user requests them. QSV hardware
encoding, latency, marker capture accuracy, Quest permissions/UX, Wi-Fi routing
on-device and real Shora tracking/reconnect behavior remain hardware-unverified.
The PCI policy includes known discrete Arc IDs; unknown/new IDs need review.
