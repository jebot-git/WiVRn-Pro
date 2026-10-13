# Quest Pro PyroWave 4:4:4 experiment

This WiVRn-Pro option adapts the Haar GPU encoder/decoder from
[JMS1717/Quest3-Pyrowave](https://github.com/JMS1717/Quest3-Pyrowave)
revision `1b529ef54b4f7b34a44c28ad9a54c6c4ac6887a7`, PyroWave base
`d2997ac172bdc00e29c58e3f2938acb7e94580bf`. Source hashes and licensing
are in `common/pyrowave/quest3-source.json`, `QUEST3-PYROWAVE-NOTICE`
and `QUEST3-PYROWAVE-LICENSE`.

The server converts the compositor output to full-resolution BT.709 YCbCr
4:4:4 and GPU-encodes Haar wavelets. The Android decoder uses Vulkan compute,
quad-packed coefficients, batched dequantization, two inverse passes per
component and GPU conversion into RGBA textures for WiVRn reprojection.
Every output pixel retains its own chroma samples. Native FP16 inverse shaders
are enabled when the Vulkan device supports them; an FP32 path remains
available. CPU code receives and parses compressed packets, with no CPU pixel
decoding or production readback. This integrates WiVRn transport and OpenXR;
it does not require ALVR or Granite at runtime.

## Selection and resolution

Build both endpoints with `WIVRN_USE_PYROWAVE=ON`. Select **PyroWave 4:4:4 Haar
(experimental)** in the client codec settings and select the `pyrowave`
encoder in the server configuration. Use matching builds: this change adds
chroma/wavelet negotiation fields and changes the WiVRn protocol hash.
Mixed PyroWave and video-codec streams are rejected, including alpha.

`profiles/quest-pro-pyrowave.json` is a server preset on port 9758.
`profiles/quest-pro-pyrowave-client.json` contains the client settings to merge
into `files/client.json` while the dedicated application is stopped. Codec
configuration uses the string `"pyrowave"`, not a numeric enum. The preset
requests 90 Hz, 400 Mbit/s aggregate, 8-bit depth, resolution and stream scale
1.0, and no spatial foveation. Bitrate is a target, not a measured guarantee.

The test Quest Pro reports OpenXR recommended resolution **1440 × 1584 per
eye**. PyroWave uses that recommendation rather than the usual WiVRn panel
resolution override. Encoded eyes are **1472 × 1600**, padded for 64-pixel
wavelet alignment. The reprojection swapchains remain 1440 × 1584.

## Builds

The tested native build is `build/native` (Release, QSV and PyroWave enabled,
server and OpenXR runtime enabled). The tested Android build uses JDK 17,
Android SDK and NDK 29.0.14206865:

```sh
./gradlew assembleDebug -Pwivrn_pyrowave=1 \
  '-Pwivrn_app_name=WiVRn Pro PyroWave' \
  -Pwivrn_version=26.9-pro-pyrowave1
cmake --build build/native --target wivrn-server openxr_wivrn pyrowave-gpu-test -j3
python3 fork/tests/performance_feed_test.py build/native
```

Main builds include the experimental option in `org.meumeu.wivrn.spatial`.
Use `-Pwivrn_pyrowave_test_app=1` to build the separate test package.
The dedicated test Android package is `org.meumeu.wivrn.pyrowave`, displayed as **WiVRn Pro
PyroWave**. It installs alongside the production spatial client. The APK is
`build/android/outputs/apk/debug/wivrn-pyrowave-debug.apk`.
Local offline builds reused the dependency cache in the steam-frame worktree;
`build/deps.cmake` and the build logs record the exact local configuration.
The host requires the normal WiVRn-Pro system dependencies, including libinput,
PipeWire and Vulkan. GCC 16 required `-Wno-error=maybe-uninitialized` for an
existing nlohmann/json warning in the telemetry publisher.

## Validation and limits

The standalone `pyrowave-gpu-test` supports `--width`, `--height`, `--stress`,
`--wire`, `--decode`, `--iterations` and `--fp16`. Desktop GPU encoding can
produce a replay file for the Android Vulkan decoder. It checks full-chroma
pixel fidelity, GPU color conversion, fragment boundaries, truncated packets
and unsupported bitstream codes. CPU readback is confined to this explicit
test executable. Quality tests use a generous payload budget, separate from
the live stream's bitrate target.

On Adreno 650, the 1440 × 1584 smooth-pattern FP32 replay measured 8.72 ms
median and 12.41 ms p95 for GPU decode plus color conversion. Plane mean
absolute error was 0.0381/255 with maximum error 1/255; GPU color conversion
matched the CPU reference exactly. A 128 × 128 high-frequency test also
passed. An FP16 replay passed fidelity checks, but its uncontrolled standalone
timing was slower; feature support alone is not evidence of a speedup.

Live HelloXR stereo streaming was tested over **5 GHz Wi-Fi 6**, with a
reported 1200 Mbit/s link. This is not a Wi-Fi 6E/6 GHz qualification and
HelloXR does not represent complex game content. The accompanying validation
report records actual received and presented frame rates, repeat counts and
latency. `PYROWAVE_GPU` client logs report shader time; server
`performance.json` reports queue-to-decoder feedback latency, so those timing
fields should not be equated. `frames` counts received source frames;
`presented_frames` counts distinct frames submitted for display, and `repeats`
counts additional submissions of those frames. Presentation feedback refers
to predicted OpenXR display time, not measured photon timing.

Keep this option experimental until representative game content sustains the
90 Hz target over the intended 6 GHz network. No headset root changes, radio
changes or production server replacement were required for these tests.

## Local live result (2026-10-13)

A 69.09-second HelloXR capture received 89.95 unique source frames/s per eye,
submitted **88.00 fresh stereo frames/s**, and repeated 1.98 frames/s at a
90 Hz display setting. Median encode-to-predicted-display latency was
48.85 ms (left) / 48.07 ms (right), with p95 73.05 / 71.47 ms. Median
queue-to-decoder feedback latency was 4.87 / 9.06 ms. These results do not
establish sustained 90 fresh frames/s or acceptable latency for every game.
The full-resolution high-frequency replay also passed on Quest Pro, with
maximum plane error 1/255 and exact GPU color conversion. That quality replay
used 5,236,712 encoded bytes for one eye and is not a 400 Mbit/s network test.

The artifact bundle under `build/artifacts/quest-pro-pyrowave` contains the
APK, host server/runtime, presets, source patch, validation and checksums.
Run `./run-test-host.sh` from the extracted bundle to create isolated runtime
and configuration directories on port 9758. It keeps normal authentication;
the local measurements used `--no-encrypt --no-instructions` for the isolated
LAN test. For a matching OpenXR app, set `XDG_RUNTIME_DIR` to the bundle's
isolated `/run/user/$(id -u)/wivrn-pyrowave-test` directory (or
`WIVRN_PYROWAVE_RUNTIME`) and `XR_RUNTIME_JSON` to its absolute
`host/openxr_wivrn.json` path. Keep the host system dependencies installed.
