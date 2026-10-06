# Stay Aligned: additional poses and anchor-validated rest

Implemented on `feature/spatial-reference-recovery`. Software tests use synthetic
orientations, acceleration and spatial snapshots; no on-body accuracy or contact
behavior has been validated.

## Pose calibration

The unified GUI Calibration menu supports standing, seated, floor/back-lying,
high kneeling, sitting on heels, belly-down with extended legs, and belly-down
with bent knees. The existing `flat` profile retains its original meaning.

1. Assign chest/hip, both thighs and both shins; check mounting directions.
2. Perform a full standing reset, then the mounting reset in the guided ski pose.
3. Save a relaxed standing pose before enabling Stay Aligned.
4. Select each additional pose to calibrate, follow its displayed instructions,
   and hold naturally still for at least one second before capture. Both feet
   are required if either foot is included. Capture rejects missing, stale,
   ambiguous, moving or sideways-heading samples.
5. Apply Stay Aligned. Export tuning TOML to retain the saved profiles at startup.

New poses require a dominant gravity-relative segment axis of at least 0.75,
matching both legs and the torso. A detected class must persist for 0.5 seconds
before posture correction starts. Ambiguity immediately suspends posture
correction; returning to a class requires another dwell. Prone profiles exclude
HMD heading from centering so a sideways head turn does not pull the torso.
Diagnostics expose the active class; `no_pose` does not mean tracking stopped.

Each profile stores the existing three symmetric yaw spreads for thighs, shins
and feet. This implementation does not add asymmetric half-kneeling templates,
individual joint-angle templates or a knee/floor contact solver. The HMD-rooted
skeleton continues tracking in unknown poses. Recognition can reduce yaw
misalignment; it cannot guarantee floor contact, eliminate knee sliding, or fix
incorrect body proportions. Rest locking remains independent of the pose class.

## Optional anchor-validated rest lock

Enable **Validate rest locks with room anchors** in Tracking settings. This is a
bounded yaw correction, not a solved-position freeze or automatic standing reset.

The spatial bridge must report at least three persisted, tracked, spatially
distributed anchors with stable identities, a valid settled reference, and fresh
HMD tracking. A two-second rest-reference window tolerates at most 2 cm of
translation and 1 degree of yaw; degenerate/collinear sets cannot establish it.
This is stricter than the broad reference-change guard. Smaller anchor movement
restarts rest validation without forcing a full standing reset.

Each sensor also requires fresh rotation and verified gravity-compensated linear
acceleration, both within 250 ms. HaritoraX serial input provides this acceleration
in m/s². UDP acceleration packets have unverified gravity/units semantics here
and cannot establish guarded rest. Missing data never means zero motion.

Rest entry needs at least one second of distinct observations, acceleration at
most 0.15 m/s², low variance, and angular speed at most 0.3 degrees/s. Exit uses
0.30 m/s² and 0.8 degrees/s, with additional checks for tilt, discontinuity and
stale data. These defaults require hardware tuning. There is no raw gyro or live
firmware rest flag in the current input interface; angular speed is derived from
quaternions. HaritoraX acceleration also depends on its orientation estimate.

At rest the target is the corrected orientation. Only yaw is corrected, at the
configured 0.01–1 degree/s limit, with at most 3 degrees of residual error and
5 degrees accumulated correction per lock. Tilt and legitimate skeletal
translation remain live. Motion or uncertainty releases the lock; offsets remain
continuous. A validated room-frame recovery rebases the target with the same yaw.
Automatic corrections do not train the reset-based drift learner.

Status includes evidence state, rotation/acceleration ages and unlock reason.
When guarded evidence is unavailable, no automatic alignment is applied; it does
not silently fall back to the old quaternion-only lock. Three stable anchors
validate the room frame, not body motion. Very slow intended rotation or
constant-speed translation can resemble rest; absolute drift prevention is not
observable from these inputs alone.

## HaritoraX controls

The **HaritoraX sensors · dongle** group selects a physical tracker and exposes:

- Sensor mode 1 (magnetometer on) or 2 (off).
- Accelerometer, gyroscope and magnetometer correction flags.
- Tracker transmission rate: 50 or 100 FPS. The skeleton update rate is unchanged.

Only fresh device reports enable writes. Apply preserves all unrelated settings,
including ankle detection and communication fields. The backend verifies a fresh
matching echo, never treats its outgoing command as confirmation, and reports
uncertainty on timeout without retries. Tracking continues while waiting. No
Bluetooth switch, automatic replay on reconnect, or startup hardware profile is
added. Device-side persistence is firmware-dependent.

A hardware change invalidates calibration/automatic yaw history for the physical
sensor and its extension. Repeat standing and mounting calibration, then recapture
relaxed poses if needed. Observed magnetometer heading/correction excludes those
sensors from server yaw drift correction and Stay Aligned to avoid competing
heading estimators. Other sensors continue following their configured policy.
