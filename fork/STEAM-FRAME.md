# Unified Quest Pro / Steam Frame development branch

This worktree is based on `feature/standalone-portability` at `5522c141`.
The Quest Pro anchoring implementation, SlimeVR/Motoc worker, keep-alive bridge,
Lighthouse controller selection, GPU encoders and WayVR integration are retained.

## Headset profiles

WiVRn streaming settings offer **Auto-detect headset**, **Quest Pro** and
**Steam Frame**. The JSON key is `headset-profile`: `auto` (default), `quest-pro`
or `steam-frame`. Auto accepts both client types; an explicit profile restricts
new connections to that type. GPU and tracking settings remain shared.

The matched client reports `seacliff` or `steam-frame`; D-Bus exposes this as
`ClientModel`. Unsupported clients are rejected. A different headset cannot take
over an existing headset's retained session; end that session before switching.

Anchor capture, cutout design, anchor-assisted rest locking and Motoc's room-anchor
mode are hidden for Frame. The server ignores Frame spatial-reference packets.
Saved Quest anchor/calibration settings are preserved. They become available
again with Quest Pro. Motoc one-time alignment, continuous rigid-tracker mode,
Index controllers and the exclusive SlimeVR/Motoc bridge selection remain available.
A saved Motoc anchor profile must be changed and saved as one-time or rigid mode
before starting calibration on Frame.

### Frame controller skeletons

The active controller profile is `/interaction_profiles/valve/frame_controller_valve`.
The `generic-trigger-squeeze` startup setting selects the local controller model;
it does not replace that interaction profile. Applications such as xrizer can use
the existing Oculus Touch binding fallback and still receive runtime hand joints.

The client now forwards controller-derived hand joints with their data source.
Previously its bare-hand input filter discarded those joints before streaming.
The server exposes them as controller-derived tracking, separately from optical
hands, so they cannot activate bare-hand gestures. Lighthouse controller overrides
continue to select their own skeletons. Frame's left D-pad routing and left/right
bindings have also been corrected.

This changes the protocol hash to `3fcff6be4c2a5f34`: rebuild both server and client
from this revision (including the Quest APK if used). The released `frame.2`
client and `frame.1` server do not include these changes. With hand tracking enabled,
the client logs `Streaming left hand joints from controller source` and the
corresponding right-hand message when the runtime supplies controller skeletons.
Packet conversion and server source-transition regression tests cover the fix;
controller animation still requires a headset retest.

## Frame client and passthrough

This is a **native Linux ARM64 client**, not an APK. Run `launch-wivrn-pro`, or
add that script to Steam. It enters **Steam Linux Runtime 4.0 (ARM64)** before
starting the client. The client release archive includes the matching ARM64 runtime.
PipeWire provides audio;
V4L2 provides H.264/HEVC decoding. Ten-bit selection follows decoder capabilities.
The headset's system settings control refresh rate if it exposes only one rate.

The local client uses OpenXR passthrough when the runtime exposes it, including
`XR_ENVIRONMENT_BLEND_MODE_ALPHA_BLEND`. In-stream **Passthrough / room → Show
room** temporarily hides the game projection and leaves the WiVRn menu available.
It resets on the next session. Lobby passthrough and streamed alpha also use the
existing compositing path. Arcturus support is conditional on its runtime exposing
that blend mode: this does not implement raw camera capture or camera discovery.
Frame spatial anchors stay unavailable until a suitable API is implemented.

### Startup library errors

The original `frame.1` launcher ran directly against SteamOS libraries. Errors
mentioning `GLIBC_2.40` in `libmvec.so.1` and `OPENSSL_3.4.0` in `libcrypto.so.3`
mean that this SDK-built client was launched outside its required runtime.
For the original archive, if the ARM64 runtime is installed in Steam, run this
from the extracted `wivrn-pro-frame` folder:

```sh
PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES=1 \
  "$HOME/.steam/root/steamapps/common/SteamLinuxRuntime_4-arm64/run" -- \
  "$PWD/launch-wivrn-pro"
```

The corrected launcher first uses the bundled `SteamLinuxRuntime_4-arm64`, then
looks for that native runtime in the default Steam library. For a separate
installation, use
`WIVRN_STEAM_RUNTIME=/path/to/SteamLinuxRuntime_4-arm64 ./launch-wivrn-pro`.
Extract the complete release archive and launch its script from a terminal or
as a native Steam shortcut. The launcher enters its own matching container.

An `Exec format error` from `pressure-vessel-wrap` after using
`SteamLinuxRuntime_4/run` means the x86 runtime launcher was selected on ARM64.
`SteamLinuxRuntime_4` and `SteamLinuxRuntime_4-arm64` are separate Valve depots.
The ARM64 helper directory inside the x86 depot is for emulation and does not
turn that depot's x86 userspace into a native ARM64 runtime. The launcher checks
both the selected userspace and the helper ELF architecture before entering it.
An already active x86 Steam Runtime container is rejected as well.

Use the full `frame.2` client archive to avoid installing a separate runtime.
Its bundled Valve runtime is pinned to `4.0.20260805.254769`, with its original
license notices and a corresponding-source URL in `STEAM-RUNTIME-SOURCE.txt`.
SteamOS system library changes are unnecessary. When launching from Steam,
leave compatibility forcing disabled so the launcher selects the native runtime.

The tracked launcher is `fork/packaging/launch-wivrn-pro`. Package staged client
files with `fork/packaging/package-frame.py`, which verifies the Valve archive
checksum and ARM64 binaries and includes the runtime and local KTX/OpenXR loader.
These changes address startup packaging; headset streaming still needs a retest.

## Experimental PyroWave

Build with `-DWIVRN_USE_PYROWAVE=ON` on server and Frame client (enabled in the
`frame` preset). Choose encoder/codec `pyrowave` in server streaming settings,
or choose PyroWave on a compatible Frame client. Automatic codec selection keeps
H.264/HEVC. Quest Pro's MediaCodec choices remain H.264/HEVC.

The codec uses Vulkan compute and eight-bit input. Vulkan features and client
codec support are checked before selection. Integration adapts the old upstream
prototype to the current per-eye encoder, two-slot ownership, timeline semaphore,
bitrate, decoder and feedback interfaces. Host-visible buffers are flushed or
invalidated explicitly, frame sizes are bounded, and partial data is discarded
when the frame changes. The prototype's unverified linear-image alias optimization
is disabled.

This remains a prototype. A local Arc A770 GPU round trip of a smooth 128×128
Y/Cb/Cr pattern passed (maximum error 1 level, mean absolute error 0.0107), including
137-byte packet fragmentation. A discontinuous edge stress pattern fails the
strict quality gate (mean 1.51, worst pixel 113 levels with the compute decoder).
The fragment decoder performed worse in that test, so the compute path is used.
These edge artifacts are unresolved; H.264/HEVC remain the recommended choices.
Neither Frame performance nor headset end-to-end streaming has been validated.

Run the hardware tests explicitly:

```sh
cmake --build build/native --target pyrowave-gpu-test
build/native/common/pyrowave/pyrowave-gpu-test
# Known strict-quality failure on the imported prototype:
build/native/common/pyrowave/pyrowave-gpu-test --stress
```

## Double vision investigation

Upstream issue [#1140](https://github.com/WiVRn/WiVRn/issues/1140) reports mismatched
right-eye projection for lobby hands/controller rays. The renderer already sends
separate view/projection matrices. Frame's Video settings now expose **Per-eye
rendering (experimental double-vision workaround)**. This renders each array layer
in a separate pass with that eye's matrices, avoiding a two-view shader invocation.
The option is off by default and does not change streamed game projections or the
Quest render path. It may cost GPU time. It is a diagnostic workaround, not a
hardware-confirmed fix; game-side stereo issues need separate investigation.

## Building

Use the upstream SDK image:
`registry.gitlab.steamos.cloud/steamrt/steamrt4/sdk/arm64:4.0.20260805.254769`.
Install KTX-Software 4.4.2 ARM64 tools, librsvg2-bin and
`@gltf-transform/cli@4.1.1`, as in `.github/workflows/Build.yml`, then run:

```sh
DESTDIR="$PWD/build/frame-install" fork/build-frame.sh -DCMAKE_INSTALL_PREFIX=/usr
```

The matching unified server is built with the existing `fork/build-server.sh`;
add `-DWIVRN_USE_PYROWAVE=ON` to include that experimental encoder. Rebuild the
Quest APK from this branch as well: upstream controller IDs and codec-capability
packets change the protocol hash. Released portability APKs are not a matching
client for this development branch.

## Provenance

Frame backports come from upstream [PR #1130](https://github.com/WiVRn/WiVRn/pull/1130)
and [PR #1139](https://github.com/WiVRn/WiVRn/pull/1139), plus subsequent upstream
codec-capability, refresh-rate and battery fixes. Original authors are preserved
in the cherry-picked commits. The fork's field-of-view packet and Quest-specific
features were retained instead of merging all upstream changes.

PyroWave source and adapters originate from upstream `proto/pyrowave`, pinned at
`a0f5700d0bae9bbe72876a59c78a385930c5c70d` (2025-09-03). Source license notices are
preserved; the encoder/decoder wrappers are adapted here. Its old Monado Vulkan
patch series was not applied: the current fork creates and configures its own
Vulkan device.

References: [Valve native-engine guidance](https://partner.steamgames.com/doc/steamhardware/steamframe/engines/custom),
[Arcturus OpenXR passthrough guidance](https://arcturus.vision/developers).

## Local validation (2026-10-11)

- GCC 16 native build: unified server, runtime, dashboard and Linux client.
- ARM64 cross build against the pinned Steam Runtime SDK using GCC 14; Frame
  client built with warnings as errors. Bundled libraries resolve inside that SDK.
- Matching optimized Quest Pro APK built with NDK 29; locally development-signed.
- Native/Android protocol constant: `5971f5c36950a3a8`.
- Offline suite passed, including anchor lifecycle, spatial transforms, cutouts,
  audio/video recovery, tracking/WayVR supervision and dashboard tests.
- Twenty-four dashboard tests and a real isolated Qt D-Bus smoke test passed.
- Integrated SlimeVR worker reconnect and Motoc exclusive-mode smoke tests passed.
- PyroWave smooth-pattern GPU/packet round trip passed on Arc A770; edge-stress
  quality failure remains documented above. No Frame headset or Arcturus hardware
  validation, and no claimed confirmation of the stereo workaround.

The original development artifacts are in `build/artifacts/frame-dev/`.
The corrected client is published as `v26.9.2-frame.2`, with local artifacts in
`build/artifacts/v26.9.2-frame.2/`. It uses the existing `frame.1` server packages
and protocol `5971f5c36950a3a8`. The Frame archive
includes a `launch-wivrn-pro` script and local KTX/OpenXR loader libraries; add the
script to Steam. The APK uses the local Android development key. The Frame prereleases do not replace the stable portability release.
