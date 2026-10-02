//! Bounded yaw-rate learning from explicit reference resets, not raw gyro bias.
use std::collections::VecDeque;
use std::time::Instant;

use nalgebra::UnitQuaternion;
use serde::Serialize;

use super::{yaw_of, yaw_quat, yaw_quat_of};

const MIN_INTERVAL: f32 = 30.0;
const MAX_INTERVAL: f32 = 1800.0;
const MAX_ANGLE: f32 = std::f32::consts::FRAC_PI_4;
const HISTORY: usize = 5;

#[derive(Debug, Clone, Serialize)]
pub struct DriftDiagnostics {
    pub enabled: bool,
    pub samples: usize,
    /// Signed corrective rate, before applying the configured strength.
    pub correction_deg_per_min: f32,
    pub horizon_secs: f32,
    pub applied_deg: f32,
    pub last_interval_secs: f32,
    pub last_reset_deg: f32,
    pub last_result: &'static str,
}

#[derive(Debug)]
pub struct DriftCompensator {
    pub enabled: bool,
    pub amount: f32,
    samples: VecDeque<(f32, f32)>, // corrective radians/second, interval seconds
    pub(super) since: Option<Instant>,
    rate: f32,
    held: Option<f32>,
    horizon: f32,
    last_interval: f32,
    last_angle: f32,
    last_result: &'static str,
}

impl DriftCompensator {
    pub(super) fn new() -> Self {
        Self { enabled: false, amount: 0.5, samples: VecDeque::new(), since: None,
            rate: 0.0, held: None, horizon: 0.0, last_interval: 0.0, last_angle: 0.0,
            last_result: "waiting_for_baseline" }
    }

    pub(super) fn clear(&mut self) {
        *self = Self { enabled: self.enabled, amount: self.amount, ..Self::new() };
    }

    pub(super) fn configure(&mut self, enabled: bool, amount: f32) {
        if self.enabled != enabled { self.clear(); }
        self.enabled = enabled;
        self.amount = amount;
    }

    pub(super) fn suspend(&mut self) { self.suspend_at(Instant::now()); }
    fn suspend_at(&mut self, now: Instant) {
        let held = self.correction_at(now);
        self.clear(); self.held = Some(held); self.last_result = "reference_suspended";
    }
    pub(super) fn record(&mut self, before: &UnitQuaternion<f32>, after: &UnitQuaternion<f32>) {
        self.record_at(before, after, Instant::now());
    }

    fn record_at(&mut self, before: &UnitQuaternion<f32>, after: &UnitQuaternion<f32>, now: Instant) {
        if !self.enabled { return; }
        self.held = None;
        let previous = self.since.replace(now);
        let dt = previous.map(|s| now.saturating_duration_since(s).as_secs_f32()).unwrap_or(0.0);
        // Quaternion difference handles +/-180 degree heading wrap correctly.
        let angle = yaw_of(&(yaw_quat_of(after) * yaw_quat_of(before).inverse()));
        self.last_interval = dt;
        self.last_angle = if angle.is_finite() { angle } else { 0.0 };
        self.last_result = if previous.is_none() {
            "baseline"
        } else if !angle.is_finite() || angle.abs() > MAX_ANGLE || dt > MAX_INTERVAL {
            // Large reference jumps and long gaps may mean remounting, power
            // cycling or a new session. Do not carry a rate through them.
            self.samples.clear();
            self.rate = 0.0;
            self.horizon = 0.0;
            "discontinuity"
        } else if dt < MIN_INTERVAL {
            // A follow-up reset reanchors time but must not replace a useful
            // rate with pose noise divided by a tiny interval.
            "too_short"
        } else {
            self.samples.push_back((angle / dt, dt));
            if self.samples.len() > HISTORY { self.samples.pop_front(); }
            self.rate = median(self.samples.iter().map(|s| s.0).collect());
            self.horizon = median(self.samples.iter().map(|s| s.1).collect());
            "accepted"
        };
    }

    fn correction_at(&self, now: Instant) -> f32 {
        if !self.enabled { return 0.0; }
        if let Some(held) = self.held { return held; }
        let elapsed = self.since.map(|s| now.saturating_duration_since(s).as_secs_f32()).unwrap_or(0.0);
        // Preserve bounded compensation: no indefinite extrapolation of an
        // old rate while the tracker warms up or changes fusion behaviour.
        (self.rate * elapsed.min(self.horizon)).clamp(-MAX_ANGLE, MAX_ANGLE) * self.amount
    }

    pub(super) fn apply(&self, rotation: UnitQuaternion<f32>) -> UnitQuaternion<f32> {
        yaw_quat(self.correction_at(Instant::now())) * rotation
    }

    pub(super) fn diagnostics(&self) -> DriftDiagnostics {
        DriftDiagnostics { enabled: self.enabled, samples: self.samples.len(), correction_deg_per_min: self.rate.to_degrees() * 60.0,
            horizon_secs: self.horizon, applied_deg: self.correction_at(Instant::now()).to_degrees(),
            last_interval_secs: self.last_interval, last_reset_deg: self.last_angle.to_degrees(),
            last_result: self.last_result }
    }
}

fn median(mut values: Vec<f32>) -> f32 {
    values.sort_by(f32::total_cmp);
    let mid = values.len() / 2;
    if values.len() % 2 == 0 { (values[mid - 1] + values[mid]) * 0.5 } else { values[mid] }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn reset(d: &mut DriftCompensator, start: Instant, secs: u64, degrees: f32) {
        d.record_at(&yaw_quat(0.0), &yaw_quat(degrees.to_radians()), start + Duration::from_secs(secs));
    }

    #[test]
    fn rate_learning_handles_unequal_intervals_and_one_bad_reset() {
        let start = Instant::now();
        let mut d = DriftCompensator::new();
        d.configure(true, 1.0);
        reset(&mut d, start, 0, 20.0); // Alignment is not drift.
        for (time, angle) in [(60, -1.0), (180, -2.0), (360, -3.0), (420, 20.0), (720, -5.0)] {
            reset(&mut d, start, time, angle);
        }
        assert_eq!(d.samples.len(), 5);
        assert!((d.diagnostics().correction_deg_per_min + 1.0).abs() < 1e-4);
        let corrected = yaw_quat(d.correction_at(start + Duration::from_secs(780))) * yaw_quat(1f32.to_radians());
        assert!(corrected.angle() < 1e-5);
        assert!((d.correction_at(start + Duration::from_secs(10000)).to_degrees() + 2.0).abs() < 1e-4);
        reset(&mut d, start, 900, -3.0);
        assert_eq!(d.samples.len(), 5);
    }

    #[test]
    fn quick_followup_does_not_poison_rate_and_gaps_clear_it() {
        let start = Instant::now();
        let mut d = DriftCompensator::new();
        d.configure(true, 1.0);
        reset(&mut d, start, 0, 0.0);
        reset(&mut d, start, 60, -1.0);
        reset(&mut d, start, 61, 10.0);
        assert_eq!(d.last_result, "too_short");
        assert_eq!(d.samples.len(), 1);
        assert!((d.correction_at(start + Duration::from_secs(121)).to_degrees() + 1.0).abs() < 1e-4);
        reset(&mut d, start, 4000, -2.0);
        assert_eq!(d.last_result, "discontinuity");
        assert_eq!(d.correction_at(start + Duration::from_secs(4060)), 0.0);
    }

    #[test]
    fn wrap_strength_and_large_reference_jump() {
        let start = Instant::now();
        let mut d = DriftCompensator::new();
        d.configure(true, 0.5);
        reset(&mut d, start, 0, 0.0);
        d.record_at(&yaw_quat(179f32.to_radians()), &yaw_quat(-179f32.to_radians()), start + Duration::from_secs(60));
        assert!((d.correction_at(start + Duration::from_secs(120)).to_degrees() - 1.0).abs() < 1e-4);
        reset(&mut d, start, 120, 90.0);
        assert_eq!(d.last_result, "discontinuity");
        assert!(d.samples.is_empty());
        d.configure(false, 1.0);
        reset(&mut d, start, 180, 1.0);
        assert!(d.since.is_none());
    }
    #[test]
    fn reference_loss_freezes_offset_and_next_reset_only_establishes_baseline() {
        let start=Instant::now(); let mut d=DriftCompensator::new();d.configure(true,1.0);
        reset(&mut d,start,0,0.0);reset(&mut d,start,60,-2.0);
        d.suspend_at(start+Duration::from_secs(90));
        assert!((d.correction_at(start+Duration::from_secs(900)).to_degrees()+1.0).abs()<1e-4);
        d.suspend_at(start+Duration::from_secs(901));
        assert!((d.correction_at(start+Duration::from_secs(902)).to_degrees()+1.0).abs()<1e-4);
        reset(&mut d,start,1000,15.0);
        assert_eq!(d.last_result,"baseline");assert!(d.samples.is_empty());assert_eq!(d.rate,0.0);
        reset(&mut d,start,1060,-1.0);assert_eq!(d.samples.len(),1);
        assert!((d.rate.to_degrees()*60.0+1.0).abs()<1e-4);
    }

}
