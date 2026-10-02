# Stay Aligned

The native server now has a Stay Aligned adaptation for HaritoraX 2's chest,
hip, thighs and shins, optional feet, and rest locking for arm/hand extensions.
It combines rest locking with gradual relaxed-pose and neighboring-segment yaw
correction. It is bidirectional: SlimeVR's positive yaw turns left from the
wearer's perspective, so the reported leftward drift produces a negative
correction. There is no unconditional rightward rotation.

## Setup in Qt

1. Build/run the updated server and `frontend/shora_qt.py`. The installed 0.1.1
   RPM does not gain this feature until replaced with a build containing it.
2. Do a **Full standing reset**, then the usual **Mounting reset**.
3. In the Calibration menu choose **Stay Aligned: save standing pose**. Stand
   naturally, look forward and hold still. Start calibration; the countdown
   gives time to settle. The server requires fresh chest or hip, both thighs,
   and both shins, and at least one second of stillness.
4. Optionally save **seated** and **floor / lying** poses using the same menu.
   Saved poses describe natural left/right leg spread. If using foot trackers,
   both feet must be available when saving. These are separate from mounting.
5. Check **Enable Stay Aligned**, leave **Alignment speed** at **0.20 °/s**
   initially, and click **Apply settings**. Selecting this unchecks reset-based
   drift correction. The server rejects requests enabling both together.
6. **Export tuning TOML…** saves the active settings and relaxed poses. Use the
   exported profile at startup or merge its `[stay_aligned]` tables into your
   existing profile; export does not include device/face configuration.

For the RPM service the startup profile is `~/.config/shora-rust/server.toml`.
The implementation does not silently edit that profile or restart the service.
Full/mounting calibration is still required after a server restart. Learned
relaxed-pose angles can be restored from TOML; accumulated yaw offsets and
rest locks are session-local.

**Clear Stay Aligned corrections** removes current offsets and rest locks but
retains the saved poses. Full, yaw, and mounting resets invalidate the relevant
automatic correction immediately. Disable Stay Aligned and perform a yaw reset
to compare against uncorrected tracking from a common reference.

## Behaviour and limits

- Rest means an uncorrected orientation stays within 2° for one second. The
  corrected orientation at that point becomes its yaw target. Moving beyond
  the rest range releases it; a three-second transition avoids immediately
  pulling a recently resting tracker toward a relaxed pose. A new rest can
  be detected during this transition.
- Moving trackers use saved standing, seated, or floor/lying leg angles, a
  weighted body heading, and adjacent segments. Corrections are rate-limited
  in degrees/second, not degrees/frame. Updates faster than 30°/s are skipped.
  A fresh HMD contributes weakly to body heading; it is never directly locked
  to every tracker. Without an external heading, coherent whole-body yaw
  error is unobservable during motion.
- Rotation freshness is tracked independently of UDP heartbeats. Samples
  older than 250 ms, duplicate body assignments, missing calibration, and
  yaw-reset easing cannot train or drive posture correction. Lost HMD input
  stops contributing to heading. A resumed sensor stream starts fresh.
  After a HaritoraX disconnect or reassignment, perform a full standing reset
  before automatic alignment resumes; saved relaxed poses are retained.
- A moving tracker's yaw is ignored near the sideways singularity. Rest
  locking instead uses a stable horizontal axis and can work while lying on
  a side. Unknown poses, including kneeling, get no relaxed-pose correction;
  rest locking can still apply. Moving arms/hands are unconstrained.
- Pause holds existing offsets and discards rest observations. Resume cannot
  integrate the paused duration into a large correction.

This is a heuristic based on expected human poses, not a new raw-IMU fusion
filter. Very slow intentional rotation can look like rest drift; an unusual
held posture or prolonged head turn can bias posture correction. The weak HMD
weight reduces head/body coupling but cannot eliminate it. Use a lower rate or
disable correction for activities where it fights intended poses. No on-body
accuracy improvement is claimed from synthetic tests alone.

The native input does not expose HaritoraX magnetometer status, so this version
cannot automatically exclude magnetometer-fused trackers as upstream does.
Use it for the drifting inertial mode; it should not compete with a reliable
absolute heading source. There is no hard-coded IMU-specific rate table.

## Control and diagnostics

The existing owner-only control socket accepts:

```json
{"command":"save_relaxed_pose","pose":"standing"}
{"command":"save_relaxed_pose","pose":"sitting"}
{"command":"save_relaxed_pose","pose":"flat"}
{"command":"clear_stay_aligned"}
```

Send the complete current `settings` object back with `set_settings`, setting
`drift_correction` to `false` and `stay_aligned.enabled` to `true` after capture.
The nested settings contain `rate_deg_per_sec` and optional `standing`, `sitting`,
`flat` objects with `upper_leg_deg`, `lower_leg_deg`, `foot_deg` fields. Missing
Stay Aligned settings default to disabled for existing clients.

Each tracker's `stay_aligned` status reports signed `correction_deg`, `rest`
state, and `mode` (`rest_lock`, `relaxed_pose`, `recently_at_rest`, `no_pose`,
`warming_up`, `stale`, `paused`, or `disabled`). A null object means there is
no observation for the current calibration. Reset journal entries now include
the pre-reset `aligned_deg` and signed remaining `residual_deg`, so subsequent
sessions can compare correction against the user's reset reference. A reset
still assumes a correct reference posture; it is not independent ground truth.

## Implementation and checks

The algorithm is adapted from upstream
[Stay Aligned](https://github.com/SlimeVR/SlimeVR-Server/tree/83941fd38e91cc91ca6b360deab5c2ae986dd1b6/server/core/src/main/java/dev/slimevr/tracking/processor/stayaligned),
with attribution in [THIRD_PARTY.md](../THIRD_PARTY.md). It updates supported
trackers from one common frame at the Rust server's 30 Hz loop, instead of
cycling one tracker at a time at the Java server's higher rate. It is not full
SlimeVR configuration/protocol parity.

Tests cover signed left/right drift at 30/90 Hz, coherent turns, common heading
error with/without an HMD, relative hip error, tilt preservation, sideways rest,
pose capture, stale/repeated samples, reset revisions, pause and heartbeat
freshness. `tests/stay_aligned_smoke.py` drives six simulated HaritoraX sensors
through a pseudo-serial GX6 and the real control/calibration loop.

Validation on 2026-10-01: all 98 Rust tests passed, the six-sensor integration
test passed, and the existing native/TUI/Qt suite passed, including exclusive
controls and exporting/reloading relaxed-pose settings. The Qt layout was also
rendered and inspected. Hardware testing on the worn trackers remains pending.

```bash
cargo test --offline --bin slimevr-server-rs
cargo build --offline --bin slimevr-server-rs
python3 tests/stay_aligned_smoke.py
python3 tests/hub_smoke.py --qt
```
