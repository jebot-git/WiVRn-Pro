# HaritoraX 2 drift investigation — 2026-10-01

Follow-up: the wearer reports drift consistently tending left. In SlimeVR's
tracking coordinates that corresponds to positive yaw, requiring negative yaw
correction. A [native Stay Aligned adaptation](stay-aligned.md) is now implemented
in the checkout. The historical observations and limitations below describe the
initial investigation; the new algorithm still requires on-body validation.

The reset-based correction can be made less sensitive to bad observations.
The available data does **not** establish a fixed gyro bias for these trackers
or demonstrate a measured improvement from the new estimator. Continuous
posture-based correction is feasible, but needs additional implementation and
on-body validation.

## Evidence from the installed system

Installed package: `slimevr-server-rs-0.1.1-1.fc44.x86_64`.
`/usr/lib/systemd/user/shora-server.service` launches `/usr/bin/slimevr-server-rs`
using `~/.config/shora-rust/server.toml`, without `--log-file`. Its stderr goes
to the user journal. The saved profile selects X2, smoothing 0.3, prediction 0,
drift correction enabled, amount 0.5. Session-local UI changes cannot be inferred
from that file.

`journalctl --user -u shora-server.service --since '7 days ago'` examined on
October 1 contained 449 entries, spanning September 24–30:

- 137 yaw resets, 46 full resets, 47 mounting resets.
- Of consecutive full/yaw reset intervals, 134 were under 30 minutes. Their
  median was 7.61 minutes; 13 intervals were under 30 seconds.
- No angular drift observations were logged. Reset frequency is an indication
  of intervention, not a measurement of sensor drift; calibration and repeated
  setup actions also contribute.

The packaged service has no separate persistent sensor recorder. The optional
TUI file path is `$XDG_RUNTIME_DIR/shora-rust.log` (or `/tmp/shora-rust.log`);
neither held a recording during this inspection. The control status's old
`drift_samples` field counts trackers with learned drift, not saved samples.
Learned calibration is process memory and is lost at server restart.

## Historical angular data, distinct from the RPM sessions

`~/.config/shora/shora.log` contains 103 Java `HumanPoseManager` angular reset
records from September 18–21: 84 yaw and 19 full resets. These predate the
current RPM sessions and must not be treated as current Rust measurements.
The Java logs in `~/.local/share/dev.slimevr.SlimeVR/logs/` overlap these records;
they were inspected separately, not added to the totals.

For a less setup-heavy subset, select yaw resets with intervals of 60–1800
seconds and no mounting reset since the preceding angular record. This leaves
48 observations for each of six trackers:

| Tracker | Median reset magnitude | Median magnitude / interval |
| --- | ---: | ---: |
| Hip | 20.81° | 3.34°/min |
| Chest | 12.18° | 1.93°/min |
| Left thigh | 10.64° | 1.90°/min |
| Left shin | 8.33° | 1.42°/min |
| Right thigh | 7.31° | 1.59°/min |
| Right shin | 5.95° | 1.50°/min |

The hip had the largest correction in 23 of 48 selected observations. It is
the first tracker to investigate, but it was not the only source of error.
Large excursions also occur elsewhere. Reset posture, strap movement,
reference changes and older coordinate/calibration code can all contribute.
Do not interpret these numbers as laboratory IMU bias measurements or as
evidence that the hip hardware is defective.

The Java logger takes the absolute yaw difference, discarding direction
([HumanPoseManager](https://github.com/SlimeVR/SlimeVR-Server/blob/main/server/core/src/main/java/dev/slimevr/tracking/processor/HumanPoseManager.kt)).
Thus these logs cannot train or replay a signed compensation rate. Zero-second
records even contain `Infinity deg/min`; those are excluded. No sensor
temperature or raw gyroscope series is present.

Reproduce the historical summary, including selected source line numbers:

```bash
python3 tools/analyze-drift-log.py ~/.config/shora/shora.log
```

## Implemented changes in this checkout

The old estimator replaced all history with the latest reset and divided its
correction by the elapsed time, including very short intervals. It saturated
after that same interval. Mounting changes and HaritoraX disconnect/reconnect
could retain an estimate tied to an obsolete reference.

The revised estimator:

1. Learns signed yaw rates from up to five intervals and takes their median.
   Rates are normalized by their individual durations. With at least three
   consistent samples, one outlier cannot dominate as it did with latest-only
   learning. Early estimates remain less robust, and changing bias can lag.
2. Ignores intervals below 30 seconds while reanchoring to the latest reset.
   A quick follow-up no longer replaces the useful rate with pose noise.
3. Clears history on gaps above 30 minutes, reset angles above 45°, mounting
   changes, and resumed HaritoraX streams. After mounting, two standing/yaw
   resets are needed: a baseline and an eligible interval.
4. Bounds extrapolation at the median measured interval and caps correction
   at 45° before multiplying by the configured strength. It does not enable
   unlimited prediction. These limits are engineering defaults, not fitted
   HaritoraX constants; repeated large genuine drift will currently be rejected.
5. Measures against the completed reference correction even during yaw easing,
   so display interpolation is not mistaken for IMU drift.
6. Adds per-tracker control-status diagnostics and info-level journal records
   named `yaw drift observation`: signed reset angle, interval, estimated
   corrective rate, sample count, horizon, previous applied correction, and
   whether an HMD reference was available. HMD availability does not certify
   freshness or correct reset posture. Signs denote the correction to apply.

Existing settings remain compatible. The changes apply when drift correction
is enabled; they have not been installed into the RPM or applied to the live
service. Synthetic tests verify estimator behaviour, not reduction in physical
tracking error. A single retained sample can still be contaminated by reset
posture; the time/angle gates cannot prove an observation is drift.

Validation: `cargo test --offline --bin slimevr-server-rs` passed all 86 tests,
and `python3 tests/hub_smoke.py` passed the hardware-free serial, calibration,
control-status, reconnect and TUI checks. Socket tests required running outside
the network-restricted sandbox. `git diff --check` passed. No on-body A/B test
has been performed.

## Additional automatic IMU correction

| Approach | Feasibility for this input | Assessment |
| --- | --- | --- |
| Rest detection plus relaxed-pose alignment | Fused orientations are available | Best next server-side feature; requires learned poses, movement gating, bounded corrections, and independent validation. |
| Knee/hip joint constraints | Separate thigh and shin orientations are available | Can reduce relative heading inconsistency during suitable motion; cannot determine whole-body absolute yaw on its own. |
| Raw gyro bias/temperature estimation | Raw gyro and temperature are absent from the current decoder | Requires additional documented device telemetry or firmware support. |
| Accelerometer-only heading correction | Acceleration is available, with gravity removed using the fused quaternion | Cannot independently observe yaw. Reusing the same quaternion's gravity estimate is not a new reference. |
| Magnetometer correction | No raw magnetic vector/status is exposed by the current decoder | Cannot implement reliable disturbance rejection or determine fusion mode from this stream. |
| HMD heading lock | HMD orientation is available | A permanent lock would miscorrect normal independent head/body turns. Use only with a validated pose/reference condition. |

Upstream SlimeVR's
[Stay Aligned](https://github.com/SlimeVR/SlimeVR-Server/blob/main/server/core/src/main/java/dev/slimevr/tracking/processor/stayaligned/adjust/AdjustTrackerYaw.kt)
combines rest locking, relaxed-pose centering and neighboring trackers. Its
[settings](https://github.com/SlimeVR/SlimeVR-Server/blob/main/gui/public/i18n/en/translation.ftl)
warn against combining it with drift compensation. A port should make the two
approaches mutually exclusive initially and account for the Rust server's
30 Hz loop. It should not blindly force every bone toward the HMD.

Although Cargo includes a `vqf` dependency, the current HaritoraX decoder returns
only an already-fused quaternion and derived acceleration. Full
[VQF](https://vqf.readthedocs.io/en/latest/) supports gyro-bias estimation and
magnetic-disturbance rejection with sensor inputs. Differentiating the fused
quaternion does not recover an independent raw gyro measurement or remove the
ambiguity between actual slow turning and yaw drift.

## Validation needed before hardware tuning

After deploying a reviewed build, collect the new signed reset observations
with stable mounting and consistent reset posture. Use matched correction-off
and correction-on sessions, recording tracker identity, body assignment and
fusion mode if known. Compare held-out reset residuals and time to next reset;
do not fit and evaluate on the same intervals. A controlled supported/stationary
run helps distinguish sensor drift from strap movement. Swapping the hip and
another tracker, if the device setup allows it, can show whether the error
follows hardware or body placement.

For a future Stay Aligned port, also record time-stamped raw fused orientations,
HMD poses, sample ages, reset events and rest-state decisions. Include slow
turns, sitting, crossing legs, bending, lying down and tracker reconnections.
Those cases are essential for detecting corrections that fight deliberate
movement. The installed RPM's existing logs cannot supply this replay dataset.
