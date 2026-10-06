//! Tracker → skeleton calibration: standing (full) reset, mounting reset, yaw
//! reset, and yaw-drift compensation. Ported from the Java server's
//! `TrackerResetsHandler` (simplified: no arm/T-pose modes, no HMD special-casing).
//!
//! Each tracker carries its own set of corrective rotations. The adjustment chain,
//! matching the Java `adjustToReference` + `adjustToDrift`, is:
//!
//! ```text
//! adjusted = drift * (yaw_fix * mount_rot_fix⁻¹ * (attachment_fix → gyro_fix →
//!             mounting_orientation → raw) * mount_rot_fix)
//! ```
//!
//! Concretely, in order: `rot = raw * mounting_orientation`, `rot = gyro_fix * rot`,
//! `rot *= attachment_fix`, `rot = mount_rot_fix⁻¹ * rot * mount_rot_fix`,
//! `rot = yaw_fix * rot`, then drift is applied on top.

use std::collections::HashMap;
use std::time::Instant;

use nalgebra::{Quaternion, UnitQuaternion, Vector3};

use crate::tracker::TrackerId;

/// Seconds over which a yaw reset eases its correction in.
const YAW_SMOOTH_SECS: f32 = 1.0;

mod drift;
use drift::DriftCompensator;
pub use drift::DriftDiagnostics;

/// Per-tracker calibration state.
#[derive(Debug)]
pub struct TrackerCalibration {
    reset_count: u64,
    alignment_revision: u64,
    full_reset_done: bool,
    position: Option<u8>,
    /// Fixed mounting orientation (tracker frame → bone frame), using SlimeVR's
    /// default mounting direction for the assigned body part.
    pub mounting_orientation: UnitQuaternion<f32>,
    gyro_fix: UnitQuaternion<f32>,
    attachment_fix: UnitQuaternion<f32>,
    mount_rot_fix: UnitQuaternion<f32>,
    yaw_fix: UnitQuaternion<f32>,
    /// The `yaw_fix` value at the moment the last yaw reset started (for easing).
    yaw_fix_start: UnitQuaternion<f32>,
    /// When the last yaw reset started (for easing the correction in).
    yaw_reset_since: Option<Instant>,
    drift: DriftCompensator,
    hardware: Option<crate::haritorax::settings::HardwareSettings>,
}

impl Default for TrackerCalibration {
    fn default() -> Self {
        Self {
            reset_count: 0,
            alignment_revision: 0,
            full_reset_done: false,
            position: None,
            mounting_orientation: default_mounting(0),
            gyro_fix: UnitQuaternion::identity(),
            attachment_fix: UnitQuaternion::identity(),
            mount_rot_fix: UnitQuaternion::identity(),
            yaw_fix: UnitQuaternion::identity(),
            yaw_fix_start: UnitQuaternion::identity(),
            yaw_reset_since: None,
            drift: DriftCompensator::new(),
            hardware: None,
        }
    }
}

impl TrackerCalibration {
    fn hardware_heading(&self) -> bool {
        self.hardware.is_some_and(|s| s.sensor_mode == 1 || s.magnetometer)
    }

    /// Full reset (standing): zero this tracker's yaw/pitch/roll against `reference`.
    ///
    /// With a correct `mounting_orientation` (chip → body) and the NED→SlimeVR
    /// parse-time conversion, `raw * mounting` is the tracker's body→world
    /// rotation (up = +Y), so the Java-compatible decomposition applies:
    /// `gyro_fix` removes the body yaw, `attachment_fix` removes pitch/roll, and
    /// `yaw_fix` aligns the yaw to the reference (HMD).
    pub fn full_reset(&mut self, raw: UnitQuaternion<f32>, reference: UnitQuaternion<f32>) {
        self.full_reset_done = true;
        self.reset_count += 1;
        self.alignment_revision += 1;
        let before = self.adjust_reference_with_yaw(raw, self.yaw_fix);

        let mounting_adjusted = raw * self.mounting_orientation;
        self.gyro_fix = inverse_yaw(&mounting_adjusted);
        self.attachment_fix = (self.gyro_fix * mounting_adjusted).inverse();
        self.yaw_fix = self.fix_yaw(mounting_adjusted, reference);
        // Full reset snaps immediately (no yaw easing).
        self.yaw_reset_since = None;

        let after = self.adjust_reference(raw);
        self.drift.record(&before, &after);
    }

    /// Yaw reset: align only the yaw to `reference`, easing the correction in
    /// over [`YAW_SMOOTH_SECS`] instead of snapping.
    pub fn yaw_reset(&mut self, raw: UnitQuaternion<f32>, reference: UnitQuaternion<f32>) {
        self.reset_count += 1;
        self.alignment_revision += 1;
        let before = self.adjust_reference_with_yaw(raw, self.yaw_fix);
        let target = self.fix_yaw(raw * self.mounting_orientation, reference);
        self.yaw_fix_start = self.yaw_fix;
        self.yaw_fix = target;
        self.yaw_reset_since = Some(Instant::now());
        // Learn the complete yaw correction, not the nearly-zero first eased step.
        let after = self.adjust_reference_with_yaw(raw, self.yaw_fix);
        self.drift.record(&before, &after);
    }

    /// Mounting reset (skip pose): compute the yaw-only axis alignment.
    ///
    /// `position` is the tracker's `TrackerPosition` id. Non-thigh trackers face
    /// "back" during a mounting reset, so their yaw angle is flipped by 180°
    /// (matching the Java `resetMounting`), while thigh trackers keep it as-is.
    pub fn mounting_reset(
        &mut self,
        raw: UnitQuaternion<f32>,
        reference: UnitQuaternion<f32>,
        position: u8,
    ) {
        self.reset_count += 1;
        self.alignment_revision += 1;

        let mut rot = raw * self.mounting_orientation;
        rot = self.gyro_fix * rot;
        rot = rot * self.attachment_fix;
        rot = self.yaw_fix * rot;
        rot = reference_yaw(&reference).inverse() * rot;

        let up = rot * Vector3::y();
        let mut yaw_angle = up.x.atan2(up.z);
        // LEFT_UPPER_LEG (7) / RIGHT_UPPER_LEG (8) are thighs; everything else
        // (chest, hip, lower legs, …) points backward in the skip pose.
        if !matches!(position, 7 | 8) {
            yaw_angle -= std::f32::consts::PI;
        }
        self.mount_rot_fix = yaw_quat(yaw_angle);
        // A mounting reset changes the bend axes while in a deliberate tilted
        // pose. It is not a measurement of yaw drift between standing resets.
        // The changed reference invalidates both the old rate and its baseline.
        self.drift.clear();
    }

    /// The corrected bone rotation: reference fixes then drift.
    pub fn adjust(&self, raw: UnitQuaternion<f32>) -> UnitQuaternion<f32> {
        self.drift.apply(self.adjust_reference(raw))
    }

    /// The reference-adjustment chain (without drift).
    fn adjust_reference(&self, raw: UnitQuaternion<f32>) -> UnitQuaternion<f32> {
        self.adjust_reference_with_yaw(raw, self.effective_yaw_fix())
    }

    fn adjust_reference_with_yaw(&self, raw: UnitQuaternion<f32>, yaw: UnitQuaternion<f32>) -> UnitQuaternion<f32> {
        let mut rot = raw * self.mounting_orientation;
        rot = self.gyro_fix * rot;
        rot = rot * self.attachment_fix;
        rot = self.mount_rot_fix.inverse() * (rot * self.mount_rot_fix);
        rot = yaw * rot;
        rot
    }

    /// The yaw fix, eased from `yaw_fix_start` to `yaw_fix` over the smooth window.
    fn effective_yaw_fix(&self) -> UnitQuaternion<f32> {
        match self.yaw_reset_since {
            Some(since) => {
                let t = (since.elapsed().as_secs_f32() / YAW_SMOOTH_SECS).clamp(0.0, 1.0);
                self.yaw_fix_start.slerp(&self.yaw_fix, t)
            }
            None => self.yaw_fix,
        }
    }

    /// Port of the Java `fixYaw`.
    fn fix_yaw(&self, sensor: UnitQuaternion<f32>, reference: UnitQuaternion<f32>) -> UnitQuaternion<f32> {
        let mut rot = self.gyro_fix * sensor;
        rot = rot * self.attachment_fix;
        rot = self.mount_rot_fix.inverse() * (rot * self.mount_rot_fix);
        let yaw = yaw_quat_of(&rot);
        yaw.inverse() * reference_yaw(&reference)
    }
}

/// The set of per-tracker calibrations, keyed by device MAC and sensor id.
pub struct Calibration {
    trackers: HashMap<TrackerId, TrackerCalibration>,
    drift_enabled: bool,
    drift_amount: f32,
    reference: Option<crate::spatial_reference::Shared>,
    reference_revision: u64,
    reference_needs_reset: bool,
    recovery_from: Option<u64>,
    pub stay_aligned: crate::stay_aligned::StayAligned,
}
impl Default for Calibration {
    fn default() -> Self {
        Self { reference: None, reference_revision: 0, reference_needs_reset: false, recovery_from: None, trackers: HashMap::new(), drift_enabled: false, drift_amount: 0.5, stay_aligned: Default::default() }
    }
}

impl Calibration {
    pub fn new() -> Self {
        Self::default()
    }

    pub fn attach_reference(&mut self, reference: crate::spatial_reference::Shared) {
        self.reference = Some(reference); self.reference_needs_reset = true;
    }
    pub fn reference_health(&self) -> Option<crate::spatial_reference::Health> {
        self.reference.as_ref().map(|r| r.read().unwrap().health(crate::spatial_reference::now_ns()))
    }
    pub fn reference_needs_reset(&self) -> bool { self.reference_needs_reset }
    pub fn sync_reference(&mut self) {
        self.sync_reference_at(crate::spatial_reference::now_ns());
    }
    fn sync_reference_at(&mut self, now: i64) {
        if let Some(reference) = &self.reference {
            let (health, from) = {
                let reference = reference.read().unwrap();
                (reference.health(now), reference.recovery_from())
            };
            if health.revision != self.reference_revision || (!health.ready && !self.reference_needs_reset) {
                self.recovery_from = if !self.reference_needs_reset && from == Some(self.reference_revision) {
                    from
                } else { None };
                for t in self.trackers.values_mut() {t.drift.suspend();}
                self.stay_aligned.suspend(Instant::now());
                self.reference_needs_reset = true;
                self.reference_revision = health.revision;
            }
        }
    }
    pub fn managed_reference(&self) -> bool { self.reference.is_some() }
    pub fn check_hmd_reference(&mut self, has_hmd: bool) {
        self.check_hmd_reference_at(has_hmd, crate::spatial_reference::now_ns());
    }
    fn check_hmd_reference_at(&mut self, has_hmd: bool, now: i64) {
        self.sync_reference_at(now);
        if self.reference.is_some() && !has_hmd {
            self.recovery_from = None;
            if !self.reference_needs_reset {
                for t in self.trackers.values_mut() {t.drift.suspend();}
                self.stay_aligned.suspend(Instant::now());
                self.reference_needs_reset = true;
            }
        } else if let Some(from) = self.recovery_from {
            let recovery = self.reference.as_ref().and_then(|r| r.read().unwrap().recovery(now));
            if let Some(recovery) = recovery.filter(|r| r.from_revision == from) {
                // Quest STAGE is also the native Monado feeder frame. The PC
                // STAGE alignment is downstream and must NOT be applied here.
                // Yaw commutes with the held drift/Stay Aligned yaw offsets.
                let delta = yaw_quat(recovery.yaw);
                for t in self.trackers.values_mut() {
                    t.yaw_fix = delta * t.yaw_fix;
                    t.yaw_fix_start = delta * t.yaw_fix_start;
                }
                self.stay_aligned.rebase(delta);
                self.reference_needs_reset = false;
                self.recovery_from = None;
                tracing::info!(yaw_deg = recovery.yaw.to_degrees(), from_revision = from,
                    revision = self.reference_revision, "spatial reference automatically rebased; no drift observation");
            }
        }
    }
    pub fn prepare_reference_reset(&mut self, has_hmd: bool) -> anyhow::Result<()> {
        self.check_hmd_reference(has_hmd);
        if let Some(health) = self.reference_health() {
            anyhow::ensure!(health.ready && has_hmd, "Reference protection: {}. Wait for stable tracking before resetting", health.state);
        }
        Ok(())
    }
    pub fn confirm_reference_reset(&mut self) {
        self.recovery_from = None;
        if self.reference.is_some() && self.reference_needs_reset {
            self.stay_aligned.clear();
            self.reference_needs_reset = false;
        }
    }
    /// The corrected bone rotation for a tracker.
    pub fn adjust(&self, id: TrackerId, raw: UnitQuaternion<f32>) -> UnitQuaternion<f32> {
        self.trackers
            .get(&id)
            .map(|t| yaw_quat(self.stay_aligned.correction(id, t.alignment_revision)) * t.adjust(raw))
            .unwrap_or(raw)
    }

    /// Get (or create) a tracker's calibration state.
    pub fn tracker_mut(&mut self, id: TrackerId) -> &mut TrackerCalibration {
        let tracker = self.trackers.entry(id).or_default();
        tracker.drift.configure(self.drift_enabled && !tracker.hardware_heading(), self.drift_amount);
        tracker
    }

    /// Set a tracker's mounting orientation from its body part (frame alignment).
    pub fn set_mounting(&mut self, id: TrackerId, position: u8) {
        let tracker = self.tracker_mut(id);
        let mounting = default_mounting(position);
        if tracker.mounting_orientation != mounting || tracker.position != Some(position) {
            tracker.alignment_revision += 1;
            tracker.full_reset_done = false;
            tracker.position = Some(position);
            tracker.mounting_orientation = mounting;
        }
    }

    pub fn configure_drift(&mut self, enabled: bool, amount: f32) {
        self.drift_enabled = enabled;
        self.drift_amount = amount;
        for tracker in self.trackers.values_mut() { tracker.drift.configure(enabled && !tracker.hardware_heading(), amount); }
    }

    pub fn clear_drift(&mut self) {
        for tracker in self.trackers.values_mut() { tracker.drift.clear(); }
    }

    pub fn is_calibrated(&self, id: TrackerId) -> bool {
        self.trackers.get(&id).is_some_and(|t| t.reset_count > 0)
    }

    pub fn drift_diagnostics(&self, id: TrackerId) -> Option<DriftDiagnostics> {
        self.trackers.get(&id).map(|t| t.drift.diagnostics())
    }

    /// Hardware heading correction and server yaw correction must not compete.
    /// Also observe external changes, including settings altered in another app.
    pub fn observe_hardware(&mut self, id: TrackerId, settings: crate::haritorax::settings::HardwareSettings) {
        let tracker = self.trackers.entry(id).or_default();
        if tracker.hardware != Some(settings) {
            tracker.hardware = Some(settings);
            self.clear_tracker_drift(id);
        }
        self.tracker_mut(id);
    }

    pub fn clear_tracker_drift(&mut self, id: TrackerId) {
        if let Some(t) = self.trackers.get_mut(&id) {
            t.drift.clear(); t.alignment_revision += 1; t.full_reset_done = false;
        }
        self.stay_aligned.invalidate(id);
    }

    pub fn alignment_diagnostics(&self, id: TrackerId) -> Option<crate::stay_aligned::Diagnostics> {
        self.trackers.get(&id).and_then(|t| self.stay_aligned.diagnostics(id, t.alignment_revision))
    }

    /// Signed residual against the reset reference, including active automatic correction.
    pub fn reset_yaw_error(&self, id: TrackerId, raw: UnitQuaternion<f32>, reference: UnitQuaternion<f32>) -> f32 {
        yaw_of(&(yaw_quat_of(&reference) * yaw_quat_of(&self.adjust(id, raw)).inverse())).to_degrees()
    }

    pub fn update_alignment(&mut self, trackers: &[crate::tracker::Tracker], hmd: Option<UnitQuaternion<f32>>, now: Instant) {
        self.sync_reference();
        self.stay_aligned.set_anchor_reference(self.reference_health().is_some_and(|h| h.rest_lock_ready));
        if self.reference_needs_reset || self.reference_health().is_some_and(|h| !h.ready) {
            self.stay_aligned.suspend(now); return;
        }
        let samples = trackers.iter().filter_map(|t| {
            let calibration = self.trackers.get(&t.id())?;
            if calibration.hardware_heading() || !calibration.full_reset_done || calibration.yaw_reset_since.is_some_and(|s| now.saturating_duration_since(s).as_secs_f32() < YAW_SMOOTH_SECS) { return None; }
            Some(crate::stay_aligned::Sample { id: t.id(), position: t.position,
                rotation: calibration.adjust_reference_with_yaw(t.rotation?, calibration.yaw_fix),
                at: t.rotation_updated_at?, revision: calibration.alignment_revision,
                acceleration: if t.accel_is_linear { t.accel.zip(t.accel_updated_at) } else { None } })
        }).collect();
        self.stay_aligned.update(samples, hmd, now);
    }

    pub fn has_drift_sample(&self, id: TrackerId) -> bool {
        self.trackers.get(&id).is_some_and(|t| t.drift.enabled && t.drift.diagnostics().samples > 0)
    }
}

// ---- Frame alignment (SlimeVR mounting orientations) ----
//
// The SlimeVR tracker's sensor frame differs from the bone frame; these constants
// (vendored from ktmath's `Quaternion.SLIMEVR`) map the two. ktmath's constructor is
// `Quaternion(w, x, y, z)` — scalar first — and each constant is a rotation about the
// Y axis (yaw), matching the physical mounting orientation.

/// Build a unit quaternion from ktmath's `(w, x, y, z)` component order.
fn q(w: f32, x: f32, y: f32, z: f32) -> UnitQuaternion<f32> {
    UnitQuaternion::new_normalize(Quaternion::new(w, x, y, z))
}

/// The `defaultMounting()` orientation for a `TrackerPosition` id — the standard
/// SlimeVR `defaultMounting()` port. The serial/UDP input boundary converts
/// the IMU world frame before these tracker-local mounting offsets are applied.
pub fn default_mounting(position: u8) -> UnitQuaternion<f32> {
    match position {
        // LEFT_LOWER_ARM, LEFT_HAND, left fingers → LEFT (90° yaw)
        13 | 17 | 21..=35 => q(0.707, 0.0, 0.707, 0.0),
        // RIGHT_LOWER_ARM, RIGHT_HAND, right fingers → RIGHT (-90° yaw)
        14 | 18 | 36..=50 => q(0.707, 0.0, -0.707, 0.0),
        // LEFT_UPPER_ARM, LEFT_LOWER_LEG → FRONT_LEFT (135° yaw)
        15 | 9 => q(0.383, 0.0, 0.924, 0.0),
        // RIGHT_UPPER_ARM, RIGHT_LOWER_LEG → FRONT_RIGHT (-135° yaw)
        16 | 10 => q(0.383, 0.0, -0.924, 0.0),
        // everything else (chest, hip, thighs, feet, neck, …) → FRONT (180° yaw)
        _ => q(0.0, 0.0, 1.0, 0.0),
    }
}

// ---- Yaw helpers ----
//
// "Yaw" is extracted the same way the Java server does: the `y` component of the
// ktmath `YZX` Euler decomposition. For a quaternion (w, x, y, z) this is
// `atan2(2(w*y - x*z), w² + x² - y² - z²)`. This matters for trackers whose IMU is
// mounted with roll/pitch (the HaritoraX legs): a forward-vector heading and the
// YZX yaw diverge by tens of degrees there, which corrupts the gyro/attachment
// fix decomposition and turns a knee bend into lateral motion. `+X` right, `+Y`
// up, `-Z` forward.

fn yaw_of(q: &UnitQuaternion<f32>) -> f32 {
    let (w, x, y, z) = (q.w, q.i, q.j, q.k);
    (2.0 * (w * y - x * z)).atan2(w * w + x * x - y * y - z * z)
}

fn yaw_quat(a: f32) -> UnitQuaternion<f32> {
    UnitQuaternion::from_axis_angle(&Vector3::y_axis(), a)
}

fn yaw_quat_of(q: &UnitQuaternion<f32>) -> UnitQuaternion<f32> {
    yaw_quat(yaw_of(q))
}

fn inverse_yaw(q: &UnitQuaternion<f32>) -> UnitQuaternion<f32> {
    yaw_quat(-yaw_of(q))
}

fn reference_yaw(q: &UnitQuaternion<f32>) -> UnitQuaternion<f32> {
    yaw_quat_of(q)
}

#[cfg(test)]
mod tests {
    #[test]
    fn hardware_heading_excludes_server_yaw_learning_and_requires_new_calibration() {
        let mut calibration = Calibration::new();
        let id = TrackerId::new([2;6], 0);
        calibration.configure_drift(true, 1.0);
        calibration.tracker_mut(id).full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
        let mut hardware = crate::haritorax::settings::HardwareSettings::decode("00000110103000").unwrap();
        calibration.observe_hardware(id, hardware);
        assert!(!calibration.tracker_mut(id).full_reset_done);
        assert!(!calibration.tracker_mut(id).drift.enabled);
        calibration.configure_drift(true, 1.0);
        assert!(!calibration.tracker_mut(id).drift.enabled);
        hardware.sensor_mode = 2;
        calibration.observe_hardware(id, hardware);
        assert!(calibration.tracker_mut(id).drift.enabled);
        assert!(!calibration.tracker_mut(id).full_reset_done);
        calibration.tracker_mut(id).full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
        calibration.observe_hardware(id, hardware);
        assert!(calibration.tracker_mut(id).full_reset_done);
    }

    #[test]
    fn automatic_rebase_preserves_pose_and_is_not_a_reset_or_drift_sample() {
        use crate::spatial_reference::{Reference, tests::{feed, room_frame, room_settle}};
        use std::sync::{Arc, RwLock};
        let shared = Arc::new(RwLock::new(Reference::default()));
        let mut now = room_settle(&mut shared.write().unwrap(), 10_000_000_000);
        let mut cal = Calibration::new();
        cal.attach_reference(shared.clone());
        cal.check_hmd_reference_at(true, now);
        let id = TrackerId::new([1; 6], 0);
        cal.configure_drift(true, 1.0);
        let t = cal.tracker_mut(id);
        t.full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
        t.drift.since = Some(Instant::now() - std::time::Duration::from_secs(60));
        t.yaw_reset(yaw_quat(0.1), UnitQuaternion::identity());
        t.yaw_reset_since = None;
        assert_eq!(t.drift.diagnostics().samples, 1);
        let reset_count = t.reset_count;
        let revision = t.alignment_revision;
        cal.confirm_reference_reset();
        // An arbitrary bent limb must retain its posture; never force it to HMD yaw.
        let raw = yaw_quat(0.4) * UnitQuaternion::from_axis_angle(&Vector3::x_axis(), 1.0);
        let before = cal.adjust(id, raw);
        for (generation, angle) in [(2, 0.3), (3, -0.4)] {
            let delta = yaw_quat(angle);
            for i in 0..=15 {
                let at = now + (i+1) * 200_000_000;
                let f = room_frame(generation*100+i as u64, at, generation, delta, Vector3::new(0.2,0.0,0.3));
                feed(&mut shared.write().unwrap(), &f, at);
                cal.check_hmd_reference_at(true, at);
                assert_eq!(cal.reference_needs_reset(), i < 10);
                if i >= 10 { assert!(cal.adjust(id, raw).angle_to(&(delta * before)) < 0.001); }
            }
            now += 16 * 200_000_000;
            let t = cal.trackers.get(&id).unwrap();
            assert_eq!(t.reset_count, reset_count);
            assert_eq!(t.alignment_revision, revision);
            assert_eq!(t.drift.diagnostics().samples, 0);
            assert_eq!(t.drift.diagnostics().last_result, "reference_suspended");
        }
        cal.tracker_mut(id).yaw_reset(raw, UnitQuaternion::identity());
        assert_eq!(cal.drift_diagnostics(id).unwrap().last_result, "baseline");
        assert_eq!(cal.drift_diagnostics(id).unwrap().samples, 0);
    }

    #[test]
    fn recovery_cannot_unlock_initial_calibration_or_hmd_loss() {
        use crate::spatial_reference::{Reference, tests::{feed, room_frame, room_settle}};
        use std::sync::{Arc, RwLock};
        for initially_calibrated in [false, true] {
            let shared = Arc::new(RwLock::new(Reference::default()));
            let now = room_settle(&mut shared.write().unwrap(), 10_000_000_000);
            let mut cal = Calibration::new();
            cal.attach_reference(shared.clone());
            cal.check_hmd_reference_at(true, now);
            if initially_calibrated { cal.confirm_reference_reset(); }
            for i in 0..=12 {
                let at = now + (i+1) * 200_000_000;
                feed(&mut shared.write().unwrap(), &room_frame(12+i as u64, at, 2, yaw_quat(0.3), Vector3::zeros()), at);
                cal.check_hmd_reference_at(i != 3, at);
            }
            assert!(cal.reference_needs_reset());
        }
    }
    use super::*;

    #[test]
    fn stay_aligned_waits_for_full_reset_after_reconnect_or_reassignment() {
        let mut calibration = Calibration::new();
        let mut registry = crate::tracker::TrackerRegistry::default();
        let id = TrackerId::new([6; 6], 0);
        let tracker = registry.ensure_local(id, 6);
        tracker.rotation = Some(UnitQuaternion::identity());
        tracker.rotation_updated_at = Some(Instant::now());
        calibration.set_mounting(id, 6);
        let trackers: Vec<_> = registry.iter().cloned().collect();
        calibration.update_alignment(&trackers, None, Instant::now());
        assert!(calibration.alignment_diagnostics(id).is_none());
        calibration.tracker_mut(id).full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
        calibration.update_alignment(&trackers, None, Instant::now());
        assert!(calibration.alignment_diagnostics(id).is_some());
        calibration.clear_tracker_drift(id);
        calibration.update_alignment(&trackers, None, Instant::now());
        assert!(calibration.alignment_diagnostics(id).is_none());
        calibration.tracker_mut(id).full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
        calibration.update_alignment(&trackers, None, Instant::now());
        assert!(calibration.alignment_diagnostics(id).is_some());
        calibration.set_mounting(id, 4); // Same mounting quaternion, different body part.
        calibration.update_alignment(&trackers, None, Instant::now());
        assert!(calibration.alignment_diagnostics(id).is_none());
    }

    #[test]
    fn mounting_reset_does_not_learn_pose_change_as_yaw_drift() {
        let mut tracker = TrackerCalibration::default();
        tracker.drift.configure(true, 0.5);
        tracker.full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
        tracker.drift.since = Some(Instant::now() - std::time::Duration::from_secs(30));
        tracker.yaw_reset(yaw_quat(0.1), UnitQuaternion::identity());
        assert_eq!(tracker.drift.diagnostics().samples, 1);
        let ski = yaw_quat(0.1) * UnitQuaternion::from_axis_angle(&Vector3::x_axis(), -0.6);
        tracker.mounting_reset(ski, UnitQuaternion::identity(), 4);
        assert_eq!(tracker.drift.diagnostics().samples, 0);
        assert!(tracker.drift.since.is_none());
    }

    #[test]
    fn drift_options_apply_to_existing_and_future_trackers_and_clear_history() {
        let mut calibration = Calibration::new();
        let existing = TrackerId::new([1; 6], 0);
        let future = TrackerId::new([2; 6], 0);
        calibration.set_mounting(existing, 4);
        calibration.configure_drift(true, 0.75);
        calibration.set_mounting(future, 9);
        for id in [existing, future] {
            let tracker = calibration.tracker_mut(id);
            assert!(tracker.drift.enabled);
            assert_eq!(tracker.drift.amount, 0.75);
            tracker.full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
            tracker.drift.since = Some(Instant::now() - std::time::Duration::from_secs(60));
            tracker.yaw_reset(yaw_quat(0.3), UnitQuaternion::identity());
            assert!(calibration.has_drift_sample(id));
            assert!(calibration.is_calibrated(id));
        }
        calibration.clear_drift();
        assert!(!calibration.has_drift_sample(existing));
        assert!(calibration.is_calibrated(existing));
        calibration.configure_drift(false, 0.25);
        assert!(!calibration.tracker_mut(future).drift.enabled);
    }

    #[test]
    fn yaw_drift_uses_completed_correction_even_while_reset_is_easing() {
        let mut tracker = TrackerCalibration::default();
        tracker.mounting_orientation = UnitQuaternion::identity();
        tracker.drift.configure(true, 1.0);
        tracker.full_reset(UnitQuaternion::identity(), UnitQuaternion::identity());
        tracker.drift.since = Some(Instant::now() - std::time::Duration::from_secs(60));
        tracker.yaw_reset(yaw_quat(0.3), UnitQuaternion::identity());
        assert!((tracker.drift.diagnostics().last_reset_deg.to_radians() + 0.3).abs() < 1e-4);
        tracker.drift.since = Some(Instant::now() - std::time::Duration::from_secs(90));
        let corrected = tracker.drift.apply(yaw_quat(0.3));
        assert!(corrected.angle() < 1e-4);
        tracker.drift.configure(true, 0.5);
        assert!((yaw_of(&tracker.drift.apply(yaw_quat(0.3))) - 0.15).abs() < 1e-4);
        tracker.drift.configure(false, 0.5);
        assert!((yaw_of(&tracker.drift.apply(yaw_quat(0.3))) - 0.3).abs() < 1e-4);
    }

    #[test]
    fn extension_calibration_does_not_change_primary_sensor() {
        let primary = TrackerId::new([1; 6], 0);
        let extension = TrackerId::new([1; 6], 1);
        let raw = yaw_quat(0.3);
        let mut calib = Calibration::new();
        calib.set_mounting(primary, 9);
        let before = calib.adjust(primary, raw);
        calib.set_mounting(extension, 10);
        calib.tracker_mut(extension).full_reset(raw, UnitQuaternion::identity());
        assert!(calib.adjust(primary, raw).angle_to(&before) < 1e-5);
        assert!(calib.adjust(extension, raw).angle() < 1e-5);
        assert!(before.angle() > 0.1);
    }

    fn yaw_close(a: &UnitQuaternion<f32>, b: &UnitQuaternion<f32>, eps: f32) -> bool {
        (yaw_of(a) - yaw_of(b)).abs() < eps
    }

    #[test]
    fn full_reset_zeroes_pitch_roll_and_aligns_yaw() {
        // A full reset removes the reset pose's pitch/roll (up becomes +Y) and
        // aligns the yaw to the reference.
        let raw = yaw_quat(1.2) * UnitQuaternion::from_axis_angle(&Vector3::x_axis(), 0.3);
        let reference = yaw_quat(0.1);

        let mut c = TrackerCalibration::default();
        c.full_reset(raw, reference);

        // Up is corrected (pitch/roll removed) …
        let adjusted = c.adjust(raw);
        let up = adjusted * Vector3::y();
        assert!((up - Vector3::y()).norm() < 1e-3, "up = {up:?}");
        // … and the yaw is aligned to the reference.
        assert!(yaw_close(&adjusted, &reference, 1e-4), "adjusted yaw {}", yaw_of(&adjusted));
    }

    #[test]
    fn full_reset_removes_pitch_and_roll() {
        // A tracker mounted with pitch + roll, but standing upright: full reset
        // should zero those out (relative to gravity "up").
        let raw = UnitQuaternion::from_axis_angle(&Vector3::x_axis(), 0.4)
            * UnitQuaternion::from_axis_angle(&Vector3::z_axis(), -0.25);
        let reference = UnitQuaternion::identity();

        let mut c = TrackerCalibration::default();
        c.full_reset(raw, reference);

        let adjusted = c.adjust(raw);
        // The rotated up vector should be ~straight up (pitch/roll removed).
        let up = adjusted * Vector3::y();
        assert!((up - Vector3::y()).norm() < 1e-3, "up = {up:?}");
    }

    #[test]
    fn yaw_reset_only_changes_yaw() {
        let raw = yaw_quat(2.0);
        let reference = yaw_quat(-0.5);

        let mut c = TrackerCalibration::default();
        c.yaw_reset(raw, reference);
        // Fast-forward past the smooth window so the correction is fully applied.
        c.yaw_reset_since = Some(Instant::now() - std::time::Duration::from_secs(2));

        let adjusted = c.adjust(raw);
        assert!(yaw_close(&adjusted, &reference, 1e-4));
    }

    #[test]
    fn yaw_reset_eases_in_over_time() {
        let raw = yaw_quat(2.0);
        let reference = yaw_quat(-0.5);

        let mut c = TrackerCalibration::default();
        c.yaw_reset(raw, reference);

        // Immediately after the reset the correction is not fully applied.
        let t0 = c.adjust(raw);
        assert!(
            !yaw_close(&t0, &reference, 1e-3),
            "correction should ease in, got yaw {}",
            yaw_of(&t0)
        );

        // After the smooth window it is fully applied.
        c.yaw_reset_since = Some(Instant::now() - std::time::Duration::from_secs(2));
        let t1 = c.adjust(raw);
        assert!(yaw_close(&t1, &reference, 1e-4), "yaw {}", yaw_of(&t1));
    }

    fn assert_mounting(p: u8, w: f32, x: f32, y: f32, z: f32) {
        let m = default_mounting(p);
        assert!((m.w - w).abs() < 1e-3, "position {p}: w = {}", m.w);
        assert!((m.i - x).abs() < 1e-3, "position {p}: i = {}", m.i);
        assert!((m.j - y).abs() < 1e-3, "position {p}: j = {}", m.j);
        assert!((m.k - z).abs() < 1e-3, "position {p}: k = {}", m.k);
    }

    #[test]
    fn default_mounting_matches_slimevr() {
        // chest/hip/thighs/feet → FRONT = (w=0, x=0, y=1, z=0) = 180° yaw
        for p in [0u8, 4, 6, 7, 8, 11, 12] {
            assert_mounting(p, 0.0, 0.0, 1.0, 0.0);
        }
        // left upper arm / left lower leg → FRONT_LEFT = (0.383, 0, 0.924, 0)
        assert_mounting(15, 0.383, 0.0, 0.924, 0.0);
        assert_mounting(9, 0.383, 0.0, 0.924, 0.0);
        // right upper arm / right lower leg → FRONT_RIGHT = (0.383, 0, -0.924, 0)
        assert_mounting(16, 0.383, 0.0, -0.924, 0.0);
        assert_mounting(10, 0.383, 0.0, -0.924, 0.0);
        // left forearm/hand → LEFT = (0.707, 0, 0.707, 0)
        assert_mounting(13, 0.707, 0.0, 0.707, 0.0);
        assert_mounting(17, 0.707, 0.0, 0.707, 0.0);
        // right forearm/hand → RIGHT = (0.707, 0, -0.707, 0)
        assert_mounting(14, 0.707, 0.0, -0.707, 0.0);
        assert_mounting(18, 0.707, 0.0, -0.707, 0.0);
    }
}
