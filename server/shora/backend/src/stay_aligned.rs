//! Stay Aligned adaptation for the native server's tracked body segments.
//! Rest locking, pose classification and centering/neighbor constraints follow
//! SlimeVR (see THIRD_PARTY.md). All updates use actual time, independent of FPS.
use std::collections::HashMap;
use std::time::Instant;
use nalgebra::{UnitQuaternion, Vector3};
use serde::{Deserialize, Serialize};
use crate::tracker::TrackerId;

const REST_ANGLE: f32 = 2.0 * std::f32::consts::PI / 180.0;
const REST_SECS: f32 = 1.0;
const RECENT_SECS: f32 = 3.0;
pub const FRESH_SECS: f32 = 0.25;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PoseKind { Standing, Sitting, Flat }

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct RelaxedPose {
    pub upper_leg_deg: f32,
    pub lower_leg_deg: f32,
    pub foot_deg: f32,
}

#[derive(Debug, Clone, Copy, PartialEq, Deserialize, Serialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub enabled: bool,
    pub rate_deg_per_sec: f32,
    pub standing: Option<RelaxedPose>,
    pub sitting: Option<RelaxedPose>,
    pub flat: Option<RelaxedPose>,
}
impl Default for Settings {
    fn default() -> Self {
        Self { enabled: false, rate_deg_per_sec: 0.2, standing: None, sitting: None, flat: None }
    }
}
impl Settings {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(self.rate_deg_per_sec.is_finite() && (0.01..=1.0).contains(&self.rate_deg_per_sec),
            "Stay Aligned rate must be between 0.01 and 1 degree/second");
        anyhow::ensure!(!self.enabled || self.standing.is_some(),
            "Save a relaxed standing pose before enabling Stay Aligned");
        for p in [self.standing, self.sitting, self.flat].into_iter().flatten() {
            anyhow::ensure!([p.upper_leg_deg, p.lower_leg_deg, p.foot_deg].iter()
                .all(|x| x.is_finite() && x.abs() <= 90.0), "Relaxed pose angles must be finite and within +/-90 degrees");
        }
        Ok(())
    }
    pub fn save(&mut self, kind: PoseKind, pose: RelaxedPose) {
        *match kind { PoseKind::Standing => &mut self.standing, PoseKind::Sitting => &mut self.sitting, PoseKind::Flat => &mut self.flat } = Some(pose);
    }
    fn pose(&self, kind: PoseKind) -> Option<RelaxedPose> {
        match kind { PoseKind::Standing => self.standing, PoseKind::Sitting => self.sitting, PoseKind::Flat => self.flat }
    }
}

#[derive(Clone, Copy)]
pub struct Sample {
    pub id: TrackerId,
    pub position: u8,
    /// Reference-adjusted, unfiltered orientation WITHOUT automatic correction.
    pub rotation: UnitQuaternion<f32>,
    pub at: Instant,
    pub revision: u64,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Rest { Moving, AtRest, RecentlyAtRest }

#[derive(Debug, Clone, Serialize)]
pub struct Diagnostics {
    pub correction_deg: f32,
    pub rest: Rest,
    pub mode: &'static str,
}

struct State {
    revision: u64,
    position: u8,
    last_sample: Instant,
    previous: UnitQuaternion<f32>,
    anchor: UnitQuaternion<f32>,
    anchor_since: Instant,
    rest: Rest,
    recent_since: Instant,
    locked: Option<UnitQuaternion<f32>>,
    correction: f32,
    mode: &'static str,
}
impl State {
    fn new(s: &Sample) -> Self {
        Self { revision: s.revision, position: s.position, last_sample: s.at, previous: s.rotation,
            anchor: s.rotation, anchor_since: s.at, rest: Rest::Moving, recent_since: s.at,
            locked: None, correction: 0.0, mode: "warming_up" }
    }
    fn observe(&mut self, s: &Sample) -> f32 {
        let dt = s.at.saturating_duration_since(self.last_sample).as_secs_f32();
        if dt <= 0.0 { return 0.0; }
        let speed = self.previous.angle_to(&s.rotation) / dt;
        self.previous = s.rotation;
        self.last_sample = s.at;
        if self.rest == Rest::RecentlyAtRest && s.at.saturating_duration_since(self.recent_since).as_secs_f32() >= RECENT_SECS {
            self.rest = Rest::Moving;
            self.anchor = s.rotation;
            self.anchor_since = s.at;
        }
        if self.anchor.angle_to(&s.rotation) > REST_ANGLE {
            if self.rest == Rest::AtRest {
                self.rest = Rest::RecentlyAtRest;
                self.recent_since = s.at;
            }
            self.anchor = s.rotation;
            self.anchor_since = s.at;
        } else if self.rest != Rest::AtRest && s.at.saturating_duration_since(self.anchor_since).as_secs_f32() >= REST_SECS {
            self.rest = Rest::AtRest;
            self.anchor = s.rotation;
            self.anchor_since = s.at;
        }
        if self.rest == Rest::AtRest {
            if self.locked.is_none() { self.locked = Some(yaw(self.correction) * s.rotation); }
        } else { self.locked = None; }
        // Rapid deliberate motion is never nudged towards a relaxed pose.
        if speed > 30f32.to_radians() { 0.0 } else { dt.min(0.1) }
    }
}

#[derive(Default)]
pub struct StayAligned {
    settings: Settings,
    states: HashMap<TrackerId, State>,
    samples: Vec<Sample>,
}
impl StayAligned {
    pub fn configure(&mut self, settings: Settings) {
        if self.settings != settings { self.clear(); }
        self.settings = settings;
    }
    pub fn clear(&mut self) { self.states.clear(); self.samples.clear(); }
    pub fn invalidate(&mut self, id: TrackerId) { self.states.remove(&id); self.samples.retain(|s| s.id != id); }
    pub fn correction(&self, id: TrackerId, revision: u64) -> f32 {
        if !self.settings.enabled { return 0.0; }
        self.states.get(&id).filter(|s| s.revision == revision).map(|s| s.correction).unwrap_or(0.0)
    }
    pub fn diagnostics(&self, id: TrackerId, revision: u64) -> Option<Diagnostics> {
        self.states.get(&id).filter(|s| s.revision == revision).map(|s| Diagnostics {
            correction_deg: s.correction.to_degrees(), rest: s.rest, mode: s.mode,
        })
    }
    /// Pause discards observations/locks, retaining the already applied offset.
    pub fn suspend(&mut self, now: Instant) {
        self.samples.clear();
        for state in self.states.values_mut() {
            state.anchor_since = now;
            state.last_sample = now;
            state.anchor = state.previous;
            state.rest = Rest::Moving;
            state.locked = None;
            state.mode = "paused";
        }
    }
    pub fn update(&mut self, mut samples: Vec<Sample>, hmd: Option<UnitQuaternion<f32>>, now: Instant) {
        samples.retain(|s| now.saturating_duration_since(s.at).as_secs_f32() <= FRESH_SECS
            && s.rotation.coords.iter().all(|v| v.is_finite())
            && matches!(s.position, 4 | 6 | 7..=12 | 15..=18));
        // Ambiguous assignments cannot be used as anatomical references.
        let mut counts = HashMap::new();
        for s in &samples { *counts.entry(s.position).or_insert(0) += 1; }
        samples.retain(|s| counts[&s.position] == 1);
        samples.sort_by_key(|s| s.position);
        let mut steps = HashMap::new();
        for s in &samples {
            let state = self.states.entry(s.id).or_insert_with(|| State::new(s));
            if state.revision != s.revision || state.position != s.position
                || s.at.saturating_duration_since(state.last_sample).as_secs_f32() > FRESH_SECS {
                *state = State::new(s);
            }
            steps.insert(s.id, state.observe(s));
            state.mode = if self.settings.enabled { "no_pose" } else { "disabled" };
        }
        // Stale input cannot build a rest lock. Keep its existing offset until
        // reconnection, so a brief missing packet doesn't itself turn the body.
        for (id, state) in &mut self.states {
            if !samples.iter().any(|s| s.id == *id) {
                state.mode = "stale";
                state.rest = Rest::Moving;
                state.locked = None;
                state.anchor_since = now;
            }
        }
        // Freeze a common frame for all corrections, avoiding iteration-order bias.
        let frame: Vec<_> = samples.iter().map(|s| Sample {
            rotation: yaw(self.correction(s.id, s.revision)) * s.rotation, ..*s
        }).collect();
        let pose = classify(&frame).and_then(|p| self.settings.pose(p));
        let center = center_yaw(&frame, hmd);
        for (s, adjusted) in samples.iter().zip(&frame) {
            let state = self.states.get_mut(&s.id).unwrap();
            if !self.settings.enabled { continue; }
            let step = self.settings.rate_deg_per_sec.to_radians() * steps[&s.id];
            let delta = match state.rest {
                Rest::AtRest => {
                    state.mode = "rest_lock";
                    state.locked.map(|target| locked_error(adjusted.rotation, target)).unwrap_or(0.0).clamp(-step, step)
                }
                Rest::RecentlyAtRest => { state.mode = "recently_at_rest"; 0.0 }
                Rest::Moving => match (pose, center, heading(&adjusted.rotation)) {
                    (Some(pose), Some(center), Some(current)) if center_weight(s.position) > 0.0 || matches!(s.position, 11 | 12) => {
                        state.mode = "relaxed_pose";
                        let offset = pose.offset(s.position);
                        let targets = neighbor_targets(s.position, &frame, pose);
                        let error = |delta: f32| {
                            let center_error = wrap(center + offset - current - delta).abs();
                            let neighbors = targets.iter().map(|target| wrap(target - current - delta).powi(2)).sum::<f32>().sqrt();
                            2.0 * center_error + neighbors
                        };
                        let baseline = error(0.0);
                        let plus = error(step);
                        let minus = error(-step);
                        if plus < baseline && plus < minus { step }
                        else if minus < baseline { -step } else { 0.0 }
                    }
                    _ => 0.0,
                },
            };
            state.correction = wrap(state.correction + delta);
        }
        self.samples = samples;
    }

    pub fn capture(&self, kind: PoseKind, now: Instant) -> anyhow::Result<RelaxedPose> {
        anyhow::ensure!(complete_body(&self.samples),
            "Cannot save relaxed pose: fresh, calibrated chest/hip, both thigh and both shin trackers are required");
        anyhow::ensure!(classify(&self.samples) == Some(kind), "{}", match kind {
            PoseKind::Standing => "Standing pose not detected. Stand upright with relaxed legs",
            PoseKind::Sitting => "Seated pose not detected. Sit upright with thighs forward and shins upright",
            PoseKind::Flat => "Floor / lying pose not detected. Lie on your back or sit on the floor with both legs extended",
        });
        for s in self.samples.iter().filter(|s| center_weight(s.position) > 0.0 || matches!(s.position, 11 | 12)) {
            anyhow::ensure!(now.saturating_duration_since(s.at).as_secs_f32() <= FRESH_SECS,
                "Fresh tracker samples are required to save a relaxed pose");
            let state = self.states.get(&s.id).unwrap();
            anyhow::ensure!(state.rest == Rest::AtRest, "Hold still for at least one second before saving a relaxed pose");
            anyhow::ensure!(heading(&s.rotation).is_some(), "A tracker is on its side; its yaw cannot be measured reliably");
        }
        let half_difference = |left, right| -> anyhow::Result<f32> {
            let find = |p| self.samples.iter().find(|s| s.position == p)
                .and_then(|s| heading(&(yaw(self.correction(s.id, s.revision)) * s.rotation)));
            match (find(left), find(right)) {
                (Some(l), Some(r)) => Ok(wrap(l - r).to_degrees() * 0.5),
                (None, None) if left == 11 => Ok(0.0),
                _ => anyhow::bail!("Both trackers in each leg/foot pair are required"),
            }
        };
        Ok(RelaxedPose { upper_leg_deg: half_difference(7, 8)?, lower_leg_deg: half_difference(9, 10)?, foot_deg: half_difference(11, 12)? })
    }
}

impl RelaxedPose {
    fn offset(self, position: u8) -> f32 {
        let angle = match position { 7 | 8 => self.upper_leg_deg, 9 | 10 => self.lower_leg_deg, 11 | 12 => self.foot_deg, _ => 0.0 };
        angle.to_radians() * if matches!(position, 8 | 10 | 12) { -1.0 } else { 1.0 }
    }
}
fn yaw(angle: f32) -> UnitQuaternion<f32> { UnitQuaternion::from_axis_angle(&Vector3::y_axis(), angle) }
fn wrap(angle: f32) -> f32 { (angle + std::f32::consts::PI).rem_euclid(std::f32::consts::TAU) - std::f32::consts::PI }
fn heading(rotation: &UnitQuaternion<f32>) -> Option<f32> {
    let right = rotation * Vector3::x();
    (right.x * right.x + right.z * right.z >= 0.25).then(|| (-right.z).atan2(right.x))
}
fn locked_error(rotation: UnitQuaternion<f32>, target: UnitQuaternion<f32>) -> f32 {
    let axis = [Vector3::x(), Vector3::y(), Vector3::z()].into_iter()
        .min_by(|a, b| (target * a).y.abs().total_cmp(&(target * b).y.abs())).unwrap();
    let actual = rotation * axis;
    let desired = target * axis;
    wrap((-desired.z).atan2(desired.x) - (-actual.z).atan2(actual.x))
}
fn center_weight(position: u8) -> f32 {
    match position { 4 | 6 => 1.0, 7 | 8 => 0.4, 9 | 10 => 0.3, _ => 0.0 }
}
fn complete_body(frame: &[Sample]) -> bool {
    frame.iter().any(|s| matches!(s.position, 4 | 6)) && (7..=10).all(|p| frame.iter().any(|s| s.position == p))
}
fn center_yaw(frame: &[Sample], hmd: Option<UnitQuaternion<f32>>) -> Option<f32> {
    if !complete_body(frame) { return None; }
    let mut x = 0.0;
    let mut z = 0.0;
    for s in frame.iter().filter(|s| center_weight(s.position) > 0.0) {
        let angle = heading(&s.rotation)?;
        x += angle.cos() * center_weight(s.position);
        z += angle.sin() * center_weight(s.position);
    }
    if let Some(angle) = hmd.and_then(|h| heading(&h)) { x += 0.5 * angle.cos(); z += 0.5 * angle.sin(); }
    (x * x + z * z > 0.01).then(|| z.atan2(x))
}
#[derive(PartialEq, Clone, Copy)]
enum Orientation { Up, Down, FrontUp, FrontDown, Side }
fn orientation(q: UnitQuaternion<f32>) -> Orientation {
    let x = (q * Vector3::x()).y;
    let y = (q * Vector3::y()).y;
    let z = (q * Vector3::z()).y;
    if x.abs() >= y.abs() && x.abs() >= z.abs() { Orientation::Side }
    else if y.abs() >= z.abs() { if y >= 0.0 { Orientation::Up } else { Orientation::Down } }
    else if z < 0.0 { Orientation::FrontUp } else { Orientation::FrontDown }
}
fn classify(frame: &[Sample]) -> Option<PoseKind> {
    use Orientation::*;
    if !complete_body(frame) { return None; }
    let torso: Vec<_> = frame.iter().filter(|s| matches!(s.position, 4 | 6)).map(|s| orientation(s.rotation)).collect();
    let at = |p| orientation(frame.iter().find(|s| s.position == p).unwrap().rotation);
    let thighs = [at(7), at(8)]; let shins = [at(9), at(10)];
    if torso.iter().chain(&thighs).chain(&shins).all(|p| *p == Up) { Some(PoseKind::Standing) }
    else if torso[0] == Up && torso.iter().all(|p| matches!(p, Up | FrontUp)) && thighs == [FrontUp; 2] && shins == [Up; 2] { Some(PoseKind::Sitting) }
    else if (torso[0] == Up || torso.iter().all(|p| *p == FrontUp)) && torso.iter().all(|p| matches!(p, Up | FrontUp))
        && thighs.iter().all(|p| matches!(p, FrontUp | Down)) && shins.iter().all(|p| matches!(p, FrontUp | Up)) { Some(PoseKind::Flat) }
    else { None }
}
fn neighbor_targets(position: u8, frame: &[Sample], pose: RelaxedPose) -> Vec<f32> {
    let torso: Vec<_> = frame.iter().filter(|s| matches!(s.position, 4 | 6)).map(|s| s.position).collect();
    let bottom = torso.last().copied();
    let neighbors: Vec<u8> = match position {
        4 | 6 => {
            let mut n: Vec<_> = torso.into_iter().filter(|p| *p != position).collect();
            if bottom == Some(position) { n.extend([7, 8]); } n
        }
        7 | 8 => bottom.into_iter().chain([position + 2]).collect(),
        9 | 10 => vec![position - 2, position + 2],
        11 | 12 => vec![position - 2],
        _ => vec![],
    };
    neighbors.into_iter().filter_map(|p| frame.iter().find(|s| s.position == p)
        .and_then(|s| heading(&s.rotation)).map(|h| wrap(h - pose.offset(p) + pose.offset(position)))).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;
    const ZERO: RelaxedPose = RelaxedPose { upper_leg_deg: 0.0, lower_leg_deg: 0.0, foot_deg: 0.0 };
    fn configured() -> StayAligned {
        let mut sa = StayAligned::default();
        sa.configure(Settings { enabled: true, standing: Some(ZERO), sitting: Some(ZERO), flat: Some(ZERO), ..Default::default() });
        sa
    }
    fn at(start: Instant, t: f32) -> Instant { start + Duration::from_secs_f32(t) }
    fn sample(position: u8, q: UnitQuaternion<f32>, at: Instant) -> Sample {
        Sample { id: TrackerId::new([position; 6], 0), position, rotation: q, at, revision: 1 }
    }
    fn body(now: Instant, q: UnitQuaternion<f32>) -> Vec<Sample> {
        [4, 6, 7, 8, 9, 10].map(|p| sample(p, q, now)).to_vec()
    }
    fn pitch(deg: f32) -> UnitQuaternion<f32> { UnitQuaternion::from_axis_angle(&Vector3::x_axis(), deg.to_radians()) }

    #[test]
    fn supported_rest_corrects_left_and_right_drift_at_different_frame_rates() {
        for hz in [30, 90] {
            for sign in [-1.0, 1.0] {
                let start = Instant::now();
                let mut sa = configured();
                let id = TrackerId::new([6; 6], 0);
                for i in 0..=(30 * hz) {
                    let t = i as f32 / hz as f32;
                    sa.update(body(at(start, t), yaw((sign * t * 0.05).to_radians())), None, at(start, t));
                }
                let correction = sa.correction(id, 1).to_degrees();
                assert!(correction * sign < -1.2, "hz={hz} correction={correction}");
                assert!((correction + sign * 1.5).abs() < 0.15);
            }
        }
    }

    #[test]
    fn pose_constraints_reduce_left_hip_error_without_changing_tilt() {
        let start = Instant::now();
        let mut sa = configured();
        let id = TrackerId::new([6; 6], 0);
        let mut last = UnitQuaternion::identity();
        for i in 0..1800 {
            let t = i as f32 / 30.0;
            let moving = pitch(8.0 * (t * 2.0).sin());
            let mut frame = body(at(start, t), moving);
            frame[1].rotation = yaw(8f32.to_radians()) * moving;
            last = frame[1].rotation;
            sa.update(frame, Some(UnitQuaternion::identity()), at(start, t));
        }
        let correction = sa.correction(id, 1);
        assert!(correction.to_degrees() < -4.0, "correction={}", correction.to_degrees());
        let corrected = yaw(correction) * last;
        assert!(((last * Vector3::y()).y - (corrected * Vector3::y()).y).abs() < 1e-6);
    }

    #[test]
    fn coherent_turns_do_not_get_pulled_to_old_heading() {
        let start = Instant::now();
        let mut sa = configured();
        for i in 0..900 {
            let t = i as f32 / 30.0;
            let turn = yaw((t * 15.0).to_radians());
            sa.update(body(at(start, t), turn), Some(turn), at(start, t));
        }
        for state in sa.states.values() { assert!(state.correction.abs() < 1e-5); }
    }

    #[test]
    fn common_heading_error_needs_external_reference_while_moving() {
        for with_head in [false, true] {
            let start = Instant::now();
            let mut sa = configured();
            for i in 0..900 {
                let t = i as f32 / 30.0;
                let q = yaw(10f32.to_radians()) * pitch(8.0 * (t * 2.0).sin());
                sa.update(body(at(start, t), q), with_head.then(UnitQuaternion::identity), at(start, t));
            }
            for state in sa.states.values() {
                if with_head { assert!(state.correction.to_degrees() < -3.0); }
                else { assert!(state.correction.abs() < 1e-4); }
            }
        }
    }

    #[test]
    fn rest_unlocks_and_sideways_rest_uses_a_stable_axis() {
        let start = Instant::now();
        let side = UnitQuaternion::from_axis_angle(&Vector3::z_axis(), std::f32::consts::FRAC_PI_2);
        assert!(heading(&side).is_none());
        assert!((locked_error(yaw(0.01) * side, side) + 0.01).abs() < 1e-5);
        let mut sa = configured();
        for i in 0..50 {
            let t = i as f32 / 30.0;
            sa.update(body(at(start, t), side), None, at(start, t));
        }
        let now = at(start, 1.7);
        sa.update(body(now, yaw(0.2) * side), None, now);
        for state in sa.states.values() {
            assert_eq!(state.rest, Rest::RecentlyAtRest);
            assert!(state.locked.is_none());
        }
    }

    #[test]
    fn repeated_stale_missing_and_revised_samples_do_not_accumulate_correction() {
        let start = Instant::now();
        let mut sa = configured();
        let mut frame = body(start, UnitQuaternion::identity());
        for i in 0..100 {
            let t = i as f32 / 30.0;
            frame = body(at(start, t), yaw((t * 0.05).to_radians()));
            sa.update(frame.clone(), None, at(start, t));
        }
        let id = frame[0].id;
        let before = sa.correction(id, 1);
        sa.update(frame.clone(), None, at(start, 3.35));
        assert_eq!(sa.correction(id, 1), before);
        sa.update(frame.clone(), None, at(start, 10.0));
        assert_eq!(sa.correction(id, 1), before);
        assert_eq!(sa.diagnostics(id, 1).unwrap().mode, "stale");
        assert!(sa.capture(PoseKind::Standing, at(start, 10.0)).is_err());
        for s in &mut frame { s.at = at(start, 11.0); s.revision += 1; }
        sa.update(frame, None, at(start, 11.0));
        assert_eq!(sa.correction(id, 2), 0.0);
        assert_eq!(sa.correction(id, 1), 0.0);
    }

    #[test]
    fn captures_standing_sitting_flat_and_rejects_partial_or_moving_setup() {
        let start = Instant::now();
        for kind in [PoseKind::Standing, PoseKind::Sitting, PoseKind::Flat] {
            let mut sa = StayAligned::default();
            let mut frame = body(start, UnitQuaternion::identity());
            for s in &mut frame {
                let tilt = match kind {
                    PoseKind::Standing => 0.0,
                    PoseKind::Sitting if matches!(s.position, 7 | 8) => 90.0,
                    PoseKind::Sitting => 0.0,
                    PoseKind::Flat => 90.0,
                };
                let spread: f32 = match s.position { 7 | 9 => 12.0, 8 | 10 => -12.0, _ => 0.0 };
                s.rotation = yaw(spread.to_radians()) * pitch(tilt);
            }
            sa.update(frame.clone(), None, start);
            assert!(sa.capture(kind, start).is_err());
            for i in 1..40 {
                let now = at(start, i as f32 / 30.0);
                for s in &mut frame { s.at = now; }
                sa.update(frame.clone(), None, now);
            }
            let pose = sa.capture(kind, at(start, 1.3)).unwrap();
            assert!((pose.upper_leg_deg - 12.0).abs() < 1e-4);
            assert!((pose.lower_leg_deg - 12.0).abs() < 1e-4);
            frame.pop();
            sa.update(frame, None, at(start, 1.3));
            assert!(sa.capture(kind, at(start, 1.3)).is_err());
        }
    }

    #[test]
    fn pause_preserves_offset_without_integrating_missing_time() {
        let start = Instant::now();
        let mut sa = configured();
        sa.update(body(start, UnitQuaternion::identity()), None, start);
        let id = TrackerId::new([4; 6], 0);
        sa.states.get_mut(&id).unwrap().correction = -0.1;
        sa.suspend(at(start, 100.0));
        sa.update(body(at(start, 100.01), UnitQuaternion::identity()), None, at(start, 100.01));
        assert!((sa.correction(id, 1) + 0.1).abs() < 0.001);
        assert_eq!(sa.states[&id].rest, Rest::Moving);
    }
}
