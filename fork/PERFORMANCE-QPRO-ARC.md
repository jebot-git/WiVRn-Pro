# Quest Pro / Arc A770 performance work

The host build script defaults to RelWithDebInfo and optimized SPIR-V. Explicit
CMake arguments can override either. The installed helper outside this repository
must also use these defaults; an empty CMAKE_BUILD_TYPE disables host optimization.

1. Set `WIVRN_PROFILE=1` in the server environment to log p50, p95 and maximum
   latency every 300 samples for each eye/alpha encoder, stereo batch, GPU copy
   and copy-fence wait. GPU intervals use hardware timestamps even without a
   Perfetto SDK. CPU encode includes sender backpressure, GPU readiness and codec
   work; these categories overlap and must not be added together. This is a
   local-stage profile, not an end-to-end or motion-to-photon measurement.
2. The headset Streaming page has a saved Low-latency decoding toggle (default
   on). It requests Android's low-latency key and Qualcomm's equivalent for QTI/
   QCOM decoders. A rejected configure is retried with a fresh codec without the
   hints. Codec acceptance does not prove the firmware honors the hints; compare
   headset decoder timestamps with the toggle on and off.
3. QSV streams use persistent concurrent workers. Set `WIVRN_PARALLEL_QSV=0`
   to restore serial encoding for A/B measurements. VAAPI/Vulkan remain serial.
   All workers join at a batch boundary before packet commit/recovery or source
   release. A failed eye/alpha encode discards the stereo batch. Codec recovery
   stays on the coordinator thread.
4. Copy removal is NOT enabled. The A770 probe found NV12 imported plane formats
   support Vulkan storage writes with modifier 0x100000000000009, but the current
   P010 R10X6/R10X6G10X6 plane formats return VK_ERROR_FORMAT_NOT_SUPPORTED for
   storage+transfer usage. A direct path needs an NV12-specific shader interface,
   encoder slot reservation before foveation, explicit external ownership and
   synchronization, and a verified render→encode→decode roundtrip. The present
   implementation reserves encoder slots after foveation, so simply pointing its
   descriptors at VA surfaces would permit overwriting a surface still encoding.
   Keep the validated copy path until those changes can be tested together.
5. Stream quality presets select existing persisted stream_scale values: 0.3
   Performance, 0.4 Balanced and 0.5 Quality. The custom foveation slider remains.
   Explicit presets keep stream size independent of eye-tracking permission;
   existing automatic defaults and saved values are preserved. Invalid gaze
   falls back to the normal centre immediately; missing gaze expires after
   100 ms measured on the host monotonic clock. Manual centre override remains.

Repeat the surface check (temporary allocations only):

```sh
c++ -std=c++20 fork/arc-surface-probe.cpp \
  $(pkg-config --cflags --libs libva libva-drm libdrm vulkan) -o /tmp/arc-surface-probe
/tmp/arc-surface-probe /dev/dri/renderD129
```

The probe exits 2 if any tested plane is unsupported; 1 indicates setup failure.
It does not validate rendering correctness or decode latency.

## Unified hand interaction

Merged `wayvr-unified-gestures.patch` from
`/home/blux/kwin/hand-tracking/unified-gestures`, authored against the earlier
WiVRn worktree. It applies to current main without dropping current changes.
Updated the pinned import hashes and cumulative managed-worker patch.

Index/middle/ring pinches map to left/middle/right click. One fist grabs a
window; two fists resize the same grabbed window about the wrist midpoint.
The matching KWin source/binary is maintained by the KWin plugin workspace.
The WayVR merge does not by itself reload the desktop compositor plugin.

## Validation boundary

Automated recovery/concurrency, gaze expiry, import and existing offline tests
are required, plus the server/WayVR and Android release builds. Hardware codec
smoke tests do not substitute for headset VR/MR visual and latency testing.
No performance percentage or full hardware validation is claimed without those
measurements. Quest Pro remains H.264/HEVC; it has no AV1 hardware decoder.
