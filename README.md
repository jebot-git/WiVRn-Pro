# WiVRn Pro

A WiVRn fork for **Meta Quest Pro over Wi-Fi and Intel Arc discrete GPUs**.
WiVRn client, server, guided spatial capture and the complete current native Shora backend are
one source tree. Both peers must be built from this branch; stock WiVRn clients
use a different protocol. The standalone spatial bridge remains on `main`.

- Intel Arc A/B and Arc Pro PCI IDs are explicitly accepted. Other GPUs, unknown
  IDs, NVIDIA NVENC, software x264 and raw encoding are rejected. Vulkan Video
  and VAAPI encode H.264/HEVC; optional FFmpeg QSV shares the selected Arc's
  VAAPI surfaces. No offload to Intel UHD or another GPU.
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
