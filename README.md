# WiVRn Pro

A WiVRn fork for **Meta Quest Pro and Steam Frame over Wi-Fi with Intel Arc, NVIDIA NVENC and AMD VAAPI**.
This development branch adds a native ARM64 Frame client and unified headset profiles; see [Frame support and known limitations](fork/STEAM-FRAME.md).
WiVRn client, server, guided spatial capture and the complete current native Shora backend are
one source tree. Both peers must be built from this branch; stock WiVRn clients
use a different protocol. The standalone spatial bridge remains on `main`.

- Intel Arc retains Vulkan Video, VAAPI and optional QSV. NVIDIA defaults to
  NVENC and AMD to VAAPI. Encoding stays on the selected Vulkan GPU; software
  encoders and Intel UHD remain excluded. H.264/HEVC remain the default codecs;
  Frame also offers an explicitly selected experimental PyroWave codec. The Arc CBR/CQP presets also support NVENC and VAAPI.
- An explicit **SlimeVR / Motoc Lighthouse** bridge-mode selector keeps the two
  tracking paths mutually exclusive. Mode changes require a WiVRn restart.
  Motoc includes rigid-tracker continuous calibration and experimental
  room-anchor continuation after initial cross-space calibration.
- The server accepts Quest Pro (`seacliff`) and the native Steam Frame client.
  Auto, Quest Pro and Steam Frame profiles are selectable in the dashboard.
  Quest anchor logic and saved calibration remain intact; Frame hides anchor controls.
  No AV1, USB tethering or ADB reverse connection controls.
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

WayVR automatically uses a compact split QWERTY keyboard while tracked hands are active.
Move each index fingertip over its half, pinch thumb to middle to type, and pinch thumb
to index to backspace. Ring pinch applies Shift to the selected key. Selection projects
along the keyboard surface normal, so wrist rotation does not steer the cursor. Controller
input restores the standard keyboard; saved placement and scale are preserved.
Close one hand into a fist over the keyboard to drag it. While holding it, close the
other hand and move the fists apart or together to resize, as with other WayVR panels.
The interaction is inspired by [Danny Yaroslavski's hand-tracking keyboard prototypes](https://medium.com/@dannyyaroslavski/designing-the-perfect-vr-keyboard-for-hand-tracking-9bd019c31ad8).

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

## Hands Frame text translation

Hands Frame photo, silent video and barcode scanning are built into WayVR.
Text recognition and translation require the separate optional
[Hands Frame OCR package](apps/hands-frame/README.md). OCR supports OpenVINO,
CUDA and ROCm GPU backends; recognized text can be translated with Google Cloud
Translation or DeepL API Free. Images stay on the PC. Without that package,
Hands Frame shows OCR translation as unavailable. Speech recognition and the
standalone subtitle application have been removed.

## Experimental desktop layer minification (WiVRn Pro)

`WIVRN_LAYER_FILTER=off|balanced|quality` selects footprint-aware sampling of
minified cylinder and quad layers in the PC compositor. The default is `off`.
Set the variable on **wivrn-pro-server**, then restart/reconnect the stream;
setting it on an OpenXR application has no effect. Unknown values select off.
Balanced uses up to 2×2 bilinear samples; quality uses up to 4×4. Sampling ramps
in from 1× to 1.5× minification. Magnified layers retain their original pixels.
Projection layers and unpremultiplied/inverted-alpha layers retain the original
sampling path. Filter taps stay within each submitted subimage, including
flipped UVs and side-by-side stereo. Gaze-driven encoding is independent.

This bounded filter reduces small-pattern aliasing; it is not an exact area
integrator or a replacement for mipmaps at extreme minification. The footprint
calculation adds work even when a layer is magnified. This is a quality option,
not a performance optimization. Leave it disabled when existing readability is
acceptable. Full-headset performance with filtering enabled has not been measured.
See `man wivrn-layer-filter` and `fork/VALIDATION.md` for measurements and limits.

Run the actual production shader's bounded Vulkan smoke test (no headset restart):

```sh
python fork/tests/layer_filter_smoke.py --monado build/monado-src
# Add --spirv-opt /path/to/spirv-opt to match optimized production builds.
```

The test requires Vulkan development files, g++, glslangValidator, and a hardware
GPU. It checks baseline/off pixel equality, unchanged magnification/projection/
alpha modes, stereo subimage boundaries, and minification fixtures. It reports
GPU timestamp distributions in three order-reversed rounds. Its 512×512 SSBO
output and synthetic overlapping layers are a microbenchmark, not end-to-end
headset performance; concurrent workloads and clock changes affect timings.
