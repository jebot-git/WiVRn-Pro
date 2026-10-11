# WiVRn Pro

A WiVRn fork for **Meta Quest Pro over Wi-Fi with Intel Arc, NVIDIA NVENC and AMD VAAPI**.
WiVRn client, server, guided spatial capture and the complete current native Shora backend are
one source tree. Both peers must be built from this branch; stock WiVRn clients
use a different protocol. The standalone spatial bridge remains on `main`.

- Intel Arc retains Vulkan Video, VAAPI and optional QSV. NVIDIA defaults to
  NVENC and AMD to VAAPI. Encoding stays on the selected Vulkan GPU; software
  encoders and Intel UHD remain excluded. H.264/HEVC and Quest Pro restrictions
  are unchanged. The Arc CBR/CQP presets also support NVENC and VAAPI.
- An explicit **SlimeVR / Motoc Lighthouse** bridge-mode selector keeps the two
  tracking paths mutually exclusive. Mode changes require a WiVRn restart.
  Motoc includes rigid-tracker continuous calibration and experimental
  room-anchor continuation after initial cross-space calibration.
- Quest Pro (`seacliff`) is checked on the device and in the server handshake.
  No AV1, USB tethering, ADB reverse connection controls or desktop XR client.
- Room capture saves up to 12 markers with a guided scan and reload verification.
  Spatial data uses WiVRn's own stage mapping and supports 32 concurrent PC
  readers. It starts automatically with the runtime, with no bridge opt-in.
- Shora's native tracker UDP/serial acquisition, skeleton/calibration, face/eye
  OSC and reconnect bridge are linked into the WiVRn executable. WiVRn supervises
  its own private tracking worker; no separate Shora installation is required.
- A unified Shora Qt6 dashboard controls streaming, pairing, tracking and profiles.
  Existing WiVRn and Shora user configurations are imported once without overwriting
  fork profiles.
- `wivrn-pro.service` manages the entire fork. Native CMake installation is the supported packaging
  route; upstream store, Flatpak, Nix and Debian release recipes were removed.

Tracking tuning and Stay Aligned relaxed poses are saved automatically to
`$XDG_CONFIG_HOME/wivrn-pro/shora.toml` (normally `~/.config/wivrn-pro/shora.toml`)
when applied in the dashboard. The worker reads this same profile on restart.
Save failures are reported without applying the change. Runtime reset offsets
and learned correction history still need fresh calibration after restarting.

In SlimeVR mode, WayVR's wrist controls use a 4×2 button grid, with Full reset and Mounting reset
in the last column. Both use a three-second countdown; press either reset button
again to cancel. The wrist panel displays the result or the tracking worker's error.
Motoc mode replaces these with Calibrate and Resume. See [portability and Motoc setup](fork/PORTABILITY.md).

See [build and service setup](docs/building.md), [encoder configuration](docs/configuration.md),
[spatial API and capture](fork/SPATIAL.md), [Shora integration](fork/SHORA.md),
[upstream updates](fork/UPSTREAM.md) and [source licenses](fork/PROVENANCE.md).

```sh
fork/build-server.sh -DCMAKE_INSTALL_PREFIX="$HOME/.local/opt/wivrn-pro"
# Optional QSV: append -DWIVRN_USE_QSV=ON
fork/build-client.sh
fork/test-offline.sh
cargo test --workspace --locked --manifest-path server/shora/Cargo.toml
```

The fork records the official WiVRn v26.9 ancestry and has an `upstream` remote.
`fork/sync-upstream.py` prepares ordinary upstream merges in isolated worktrees.
CI builds QSV OFF/ON and the Quest Pro APK, runs offline tests, and checks weekly
for new upstream commits once this branch is hosted as the default GitHub branch.

**Live headset tests remain suspended.** Build and simulated test results do not
establish hardware encoding performance, capture accuracy or on-device UX.
WiVRn and the integration are GPL-3.0-or-later; Shora bridge sources retain their
MIT/Apache-2.0 licenses. Original author and bundled dependency notices remain.

## Optional speech and scene translation

Speech subtitles, voice colours, and scene OCR are now an independent [Spatial Subtitles OpenXR overlay](apps/spatial-subtitles/README.md). Recognition uses local OpenVINO models on the Intel GPU; translation can use local models or DeepL API Free, with English and Serbian output and Japanese, Korean, Chinese, and Thai support. Configure and disable them in the separate application; WiVRn no longer owns their pipeline or GUI.
