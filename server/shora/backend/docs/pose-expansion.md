# Stay Aligned: kneeling and prone pose exploration

Scope: code inspection of the bundled backend, with a proposed implementation
sequence. The reference-recovery branch does not enable new pose classes. No
on-body accuracy or contact detection has been validated.

## Current behavior

`stay_aligned.rs` offers `standing`, `sitting`, and `flat`. Classification uses the
dominant vertical component of each segment's local axes, not joint angles or
contact sensing. Chest or hip, both thighs and both shins are required. The flat
rule uses `FrontUp`/upright torso orientations; it does not cover `FrontDown`
(belly-down torso). High kneeling (upright torso/thighs, shins pointing back along
the floor) and sitting on the heels also lack a matching rule.

Unrecognized posture does not stop skeleton tracking. Fresh tracker rotations
still drive forward kinematics, and Stay Aligned rest locking works independently
of the relaxed-pose classifier. It can constrain a sufficiently stationary prone
or kneeling tracker after one second within the rest range. Moving trackers in
these postures have no relaxed-pose correction. This distinction should be visible
in the UI: `no_pose` is not a tracker disconnection.

The current saved pose stores only three symmetric yaw spreads: upper legs, lower
legs and feet. It cannot describe asymmetric kneeling or the different segment
tilts of straight-legged versus bent-knee prone poses. Its heading uses the local
right axis projected onto the horizontal plane. This works for ideal prone pitch,
but becomes unreliable when that axis becomes vertical during a side roll. Rest
locking already chooses a stable axis; moving-pose correction does not.

## Recommended pose additions

| Pose | Distinguishing evidence | Correction approach |
| --- | --- | --- |
| High kneeling | Upright torso/thighs, approximately horizontal shins extending backward | Separate thigh and shin yaw targets; retain torso heading |
| Sitting on heels | Lower pelvis, folded legs; thigh/shin relationship differs from high kneeling | Separate profile, not a wider sitting threshold |
| Prone, legs extended | Belly-down torso and thighs, approximately extended knees | Torso/neighbor constraints; suppress HMD heading influence |
| Prone, knees bent | Belly-down torso/thighs, elevated lower legs | Separate shin template; suspend heading correction near singularities |
| Half kneeling, left/right | Different leg configurations | Later addition requiring per-side templates |

Start with high kneeling and straight-legged prone. Keep the existing flat
profile compatible and label it more precisely rather than silently treating it
as belly-down calibration. Do not simply accept `FrontDown` in every flat rule:
mixed postures and pose transitions would receive inappropriate targets.

## Detection and calibration design

1. Compute continuous gravity-relative segment features and relative knee/hip
   angles from calibrated orientations. Match a bounded anatomical envelope,
   then score distance to the selected saved template. Do not classify by yaw
   error alone: yaw is the quantity being corrected.
2. Add confidence and hysteresis: a stable entry dwell, wider exit envelope and
   a transition/unknown state. Unknown or rapidly changing posture holds offsets
   and permits rest locking, but does not force a neighboring pose class.
3. Extend saved data to optional per-side segment tilt templates and yaw targets
   relative to the torso. Preserve old three-angle profiles and schema defaults.
   Capture a short window of fresh, still samples with variance checks. Reject
   ambiguous heading measurements rather than saving unstable angles.
4. Reduce or disable HMD heading contribution for prone poses: looking sideways
   to breathe or at a screen must not twist the body. Use torso and adjacent
   segments; accept that common whole-body IMU yaw drift then remains unobservable
   without another body reference. Spatial room anchors do not supply that reference.
5. Use a pose-specific heading axis with continuity checks, or withhold moving
   correction where the horizontal projection is weak. Blindly switching axes
   risks a 90/180-degree heading discontinuity.

Initial profile capture should remain deliberate. A known floor and HMD height
can help distinguish low poses but cannot prove kneeling, foot contact, or body
direction. Beds, cushions, furniture and crouching break a simple height threshold.
The existing bridge exports scene geometry, but Shora's reference guard does not
consume floor planes. A separate frame-aware geometry input would be required.

## Maintaining tracking versus correcting yaw

The solver currently pins the root to HMD position and propagates calibrated
bone rotations. It has no knee/floor contact solver or reliable foot-lock state.
Consequently, better pose recognition can reduce yaw distortion but will not by
itself eliminate knee sliding, floor penetration, or body-proportion errors.

Contact constraints should be a later, separately tested stage. Infer contact
only with sustained low velocity and consistent height against a known surface;
release it on deliberate movement. Do not clamp every low knee or foot to STAGE
floor zero. Lack of positional body measurements and changing support surfaces
make those constraints uncertain. Keep solving the measured skeleton while
classification is unknown or one optional segment is missing; do not freeze all
body output to maintain an appearance of stability.

## Implementation sites and validation

Backend changes would touch `PoseKind`, `Settings`, `RelaxedPose`, `classify`,
`capture`, `center_yaw` and neighbor targets in `src/stay_aligned.rs`. Update config
round-trip validation, control requests, tuning export and the Qt pose menu/status
together. Existing standing/sitting/flat profiles must deserialize unchanged.

Tests should cover ideal and perturbed kneeling/prone poses, bent knees, asymmetric
legs, side rolls, crawling, transitions through sitting, sustained head turns,
missing/stale samples and duplicate assignments. Verify signed drift correction,
unchanged pitch/roll, frame-rate independence, no correction during deliberate
turns, and compatibility with automatic room-frame recovery. Add simulated
serial/control tests, then record headset sessions with known poses to measure
false recognition, false correction, and contact errors separately.

Suggested order: continuous classifier and diagnostics; deliberate capture of
high kneeling/prone templates; bounded posture correction; optional asymmetric
profiles; finally contact constraints if measurements support them.

## Feasibility of anchor-validated rest locking

Proposal: with at least three localized stable anchors and affirmative tracker
rest evidence, reject orientation drift while the tracker remains at rest.
This is feasible as an extension of Stay Aligned's existing rest lock, including
for kneeling and prone postures that have no relaxed-pose profile. It does not
require forcing the user into a standing reset pose.

The current inputs impose limits:

- `Tracker` stores a quaternion and optional acceleration, but no gyro sample,
  live firmware rest indication, or acceleration-specific timestamp. UDP supports
  acceleration packets; HaritoraX's decoder computes gravity-compensated
  acceleration from its decoded gravity vector and quaternion. Stay Aligned's
  `Sample` currently receives neither acceleration nor a rest flag.
- Missing packets are unknown motion, not zero motion. The `SensorInfo`
  rest-calibration field is not a continuously updated stillness measurement and
  is not used as one by this backend.
- A raw accelerometer at rest normally measures gravity; a properly compensated
  linear-acceleration signal is near zero. Units, axes and gravity-removal semantics
  must be identified per input before using thresholds. HaritoraX compensation
  uses the same orientation estimate, so it is not fully independent evidence.
- Near-zero linear acceleration does not exclude constant-speed translation or
  slow rotation. Gyro readings also need a noise/bias model: a gyro bias that causes
  yaw drift may itself be the small nonzero signal under test.

Recommended algorithm:

1. Require fresh, tracked persisted anchors with stable identities and a settled
   reference, plus fresh sensor observations. Three anchors being present is
   insufficient while they are moving, stale, or being relocalized.
2. Establish a rest candidate using a rolling window of orientation change,
   calibrated acceleration magnitude/variance, and fresh gyro or a reliable live
   firmware rest flag if that input becomes available. Use dwell and hysteresis.
3. On rest entry, save the **corrected orientation** in the room/feeder frame.
   While rest confidence remains high, counter small yaw deviations against that
   saved target. Preserve legitimate pitch/roll and use the existing stable-axis
   error calculation for side/prone poses. Use a rate limit and a bounded
   discrepancy; an unexplained large jump should suspend the lock, not trigger
   an automatic standing reset.
4. Release promptly on affirmative movement, stale data, reference uncertainty,
   remounting or reassignment. Preserve the accumulated correction when releasing
   so movement does not undo the drift correction. During an accepted frame rebase,
   transform the target by the same yaw; during an uncertain transition discard
   rest observations and reacquire them.
5. Record diagnostics: rest confidence, observation ages, rejected yaw, and unlock
   reason. Keep automatic corrections out of the reset-based drift learner.

The current 2-degree orientation rest range can be exceeded by cumulative drift
even when the physical sensor stays stationary. Better independent rest evidence
could allow the lock to remain active across that drift. With only quaternion
and acceleration, this remains a heuristic: very slow intended yaw can still be
indistinguishable from drift. The extra anchors validate the coordinate frame;
they do not resolve that missing body-motion observation.

Do not discard every change in the tracker's **solved position**. Slime/HaritoraX
body inputs here are orientations; skeleton joint positions are inferred from
the HMD root, neighboring segment rotations and bone lengths. A stationary
orientation can accompany legitimate translation. Hard position locking would
require explicit contact constraints and a solver that can reconcile the HMD
and body measurements. It would otherwise create stretched or frozen limbs.

A sensible follow-up implementation is fresh acceleration plumbing plus an
opt-in anchor-validated yaw rest lock. Add raw gyro/live rest capability only
where the source actually supplies it; fall back explicitly to heuristic mode.
Test slow intentional turns, slow translation, impacts, missing packets, changing
gravity estimates, belly-down head turns and room-frame recovery before increasing
correction authority. This branch documents that extension rather than enabling
an unvalidated hard freeze or automatic per-tracker reset.
