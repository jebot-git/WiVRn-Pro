# Standalone portability branch

This branch starts from WiVRn-Pro `84fad670`. It adds GPU backends and an optional
tracking provider without changing the Quest Pro client, audio, stream protocol,
spatial capture, compositor algorithms, face/eye relay or default SlimeVR mode.
The related Arc preset/CQP inputs were imported from the active local checkout;
its other uncommitted work is not part of this branch.

## Setup

1. Build/install as described in `docs/building.md`. NVENC and Lighthouse/Motoc
   build support default ON. QSV remains an optional build feature.
2. In **WiVRn streaming**, select **SlimeVR bridge** or **Motoc / Lighthouse bridge**.
   Save and restart WiVRn using the existing dashboard restart button. The runtime
   and tracking worker use the same mode for their entire lifetime; they cannot
   simultaneously acquire/output trackers through both paths.
3. For Lighthouse, install the compatible SteamVR Lighthouse driver and configure
   USB access. Power trackers before runtime startup. Previously discovered
   devices retain their runtime identities across headset sleep/reconnect;
   discovering a device absent at initial startup may require a runtime restart.
4. In **Motoc / Lighthouse**, enable Motoc, enter the exact Quest device serial
   (`WiVRn HMD` by default) and Lighthouse tracker serial (`LHR-…`), choose a mode,
   and save. Calibrate or resume the saved profile. These commands use bundled
   Motoc bound to WiVRn's matching runtime, without starting SteamVR as the runtime.
5. Optionally select **Auto-detect system xrizer** in streaming settings. Manual
   compatibility paths and explicit disable remain available. xrizer translates
   OpenVR applications; the Lighthouse hardware driver is a separate dependency.

The Motoc tab also provides **Use Lighthouse controllers when available**
(`motoc-lighthouse-controllers`, default true). Detected Index/Lighthouse
controllers take their assigned hands; any remaining hand uses its headset
controller. Disable this option to keep headset controllers with Lighthouse
trackers. Save **WiVRn settings** and restart WiVRn to change controller roles.
Pair and power controllers before startup. Their poses use the same calibrated
Lighthouse origin as trackers, and headset tracking updates preserve their native
interaction profiles and bindings. Controller selection stays fixed during a
headset reconnect; this is not automatic switching on controller tracking loss.

The wrist panel keeps its existing four-column layout. In Motoc bridge mode the
last column becomes **Calib** / **Resume**, with Motoc instructions and the same
three-second cancelable countdown. In SlimeVR mode it retains Full/Mounting reset.
Commands queued for a replaced worker or a different provider are rejected.

## Keep-alive ownership

The WiVRn runtime retains Lighthouse's native device system and driver context
until runtime teardown, rather than reopening dongles at each headset reconnect.
The existing tracking-worker supervisor owns Motoc and its private control API.
The keep-alive option resumes saved continuous calibration after a worker/process
failure, with bounded retry delays. Initial sampling is never blindly replayed;
a readiness signal enables retry only after calibration produced an alignment.
Stop terminates and reaps that child; changing options stops it before applying
new settings. Descendants are killed if the worker dies. SlimeVR acquisition,
SolarXR and the SlimeVR reconnect proxy are disabled in Motoc mode.

## Can a spatial anchor replace a rigid tracker?

Partially. **Rigid continuous mode** observes two devices attached together and
can correct their relative alignment as they move. A Quest anchor is observed
only in Quest's coordinate system; it has no measurement of Lighthouse space.
It cannot bootstrap the cross-space transform or detect moved base stations.

The experimental **room anchors** mode first runs normal one-shot Motoc sampling.
It then captures three or more persisted, spatially distributed room markers and
the calibrated Quest-to-Lighthouse transform. Wait for “Room anchor reference
saved” in the service log before detaching the tracker. Subsequent anchor poses
provide a yaw/translation reference for continuing that alignment, without a
tracker permanently mounted on the headset. Recalibrate if base stations move,
the room reference changes, or the initial calibration was poor.

For saved anchor poses A₀ and current poses Aₜ in raw Quest stage coordinates,
the consensus transform Δ maps A₀ to Aₜ. The Lighthouse origin becomes
`root_from_Quest(now) × Δ × Quest(saved)_from_Lighthouse`.
Using raw Quest coordinates avoids applying the PC stage mapping twice. This
updates only the Lighthouse origin; it does not shift WiVRn's HMD or global STAGE.
It is an anchor-based continuation method, not a fabricated physical tracker.

Every common marker must agree within 2 cm and 1°; the positional layout must
contain a non-degenerate triangle. A transform must settle for two seconds within
1 cm / 0.5°. Old/repeated samples, missing anchors, tracking loss, changed epochs,
outliers, tilt and socket loss suspend corrections. Fresh matching references can
settle and recover after a headset reconnect. Corrections use yaw and translation;
roll/pitch discrepancies are rejected. A single anchor is deliberately insufficient.

Saved profiles live in the normal Motoc config directory under distinct
`wivrn-pro-once`, `wivrn-pro-rigid`, and `wivrn-pro-anchors` names. Anchor baselines
are atomically saved as `wivrn-pro-anchors.anchors.json`. Resume validates the
configured device identities. `shora.toml` stores only mode/device/toggle settings.

Anchor noise, relocalization and a two-second settle delay can make this less
accurate or responsive than a rigid Lighthouse tracker. No hardware accuracy,
latency or reconnect-performance claim is established by the simulated tests.

## Encoder presets

Arc's original CBR and experimental CQP preset data are available with QSV,
NVENC and VAAPI backend choices. CQP means variable **uncapped** traffic, not
average-bitrate VBR. QP has the same numeric range but equal values across
encoders do not promise identical quality or bandwidth. GPU-side validation,
maximum sizes and runtime encoding still require supported drivers/hardware.
NVENC matches CUDA to Vulkan by UUID, including multi-GPU systems. VAAPI/QSV
must use the selected Vulkan GPU's DRM render node. QSV remains Arc-only.

## Validation

Run `fork/test-offline.sh`, `fork/test-portability.sh`, and the locked Shora,
Motoc and WayVR Cargo tests. These do not connect to a headset or modify services.
Live Quest Pro, NVIDIA/AMD encoding, Lighthouse dropout/reconnect, calibration
accuracy and anchor drift remain hardware validation tasks.
