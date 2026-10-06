//! Stay Aligned adaptation for the native server's tracked body segments.
//! Rest locking, pose classification and centering/neighbor constraints follow
//! SlimeVR (see THIRD_PARTY.md). All updates use actual time, independent of FPS.
use std::collections::{HashMap, VecDeque};
use std::time::Instant;
use nalgebra::{UnitQuaternion, Vector3};
use serde::{Deserialize, Serialize};
use crate::tracker::TrackerId;

const REST_ANGLE: f32 = 2.0 * std::f32::consts::PI / 180.0;
const REST_SECS: f32 = 1.0;
const RECENT_SECS: f32 = 3.0;
pub const FRESH_SECS: f32 = 0.25;
const REST_ACCEL_ENTER: f32 = 0.15; // gravity-compensated m/s²
const REST_ACCEL_EXIT: f32 = 0.30;
const REST_ACCEL_VARIANCE: f32 = 0.01; // (m/s²)², sum across axes
const REST_SPEED_ENTER: f32 = 0.3 * std::f32::consts::PI / 180.0;
const REST_SPEED_EXIT: f32 = 0.8 * std::f32::consts::PI / 180.0;
const REST_MAX_ERROR: f32 = 3.0 * std::f32::consts::PI / 180.0;
const REST_MAX_CORRECTION: f32 = 5.0 * std::f32::consts::PI / 180.0;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PoseKind { Standing, Sitting, Flat, Kneeling, Heels, Prone, ProneBent }

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
    /// Opt-in: requires stable room anchors and verified fresh linear acceleration.
    pub anchor_rest_lock: bool,
    pub rate_deg_per_sec: f32,
    pub standing: Option<RelaxedPose>,
    pub sitting: Option<RelaxedPose>,
    pub flat: Option<RelaxedPose>,
    pub kneeling: Option<RelaxedPose>,
    pub heels: Option<RelaxedPose>,
    pub prone: Option<RelaxedPose>,
    pub prone_bent: Option<RelaxedPose>,
}
impl Default for Settings {
    fn default() -> Self {
        Self { enabled: false, anchor_rest_lock: false, rate_deg_per_sec: 0.2, standing: None, sitting: None, flat: None,
            kneeling:None, heels:None, prone:None, prone_bent:None }
    }
}
impl Settings {
    pub fn validate(&self) -> anyhow::Result<()> {
        anyhow::ensure!(self.rate_deg_per_sec.is_finite() && (0.01..=1.0).contains(&self.rate_deg_per_sec),
            "Stay Aligned rate must be between 0.01 and 1 degree/second");
        anyhow::ensure!(!self.enabled || self.standing.is_some(),
            "Save a relaxed standing pose before enabling Stay Aligned");
        for p in [self.standing, self.sitting, self.flat, self.kneeling, self.heels, self.prone, self.prone_bent].into_iter().flatten() {
            anyhow::ensure!([p.upper_leg_deg, p.lower_leg_deg, p.foot_deg].iter()
                .all(|x| x.is_finite() && x.abs() <= 90.0), "Relaxed pose angles must be finite and within +/-90 degrees");
        }
        Ok(())
    }
    pub fn save(&mut self, kind: PoseKind, pose: RelaxedPose) {
        *match kind { PoseKind::Standing => &mut self.standing, PoseKind::Sitting => &mut self.sitting, PoseKind::Flat => &mut self.flat,
            PoseKind::Kneeling=>&mut self.kneeling, PoseKind::Heels=>&mut self.heels, PoseKind::Prone=>&mut self.prone, PoseKind::ProneBent=>&mut self.prone_bent } = Some(pose);
    }
    fn pose(&self, kind: PoseKind) -> Option<RelaxedPose> {
        match kind { PoseKind::Standing => self.standing, PoseKind::Sitting => self.sitting, PoseKind::Flat => self.flat,
            PoseKind::Kneeling=>self.kneeling, PoseKind::Heels=>self.heels, PoseKind::Prone=>self.prone, PoseKind::ProneBent=>self.prone_bent }
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
    /// Verified linear acceleration only, in m/s². None means unknown, not rest.
    pub acceleration: Option<(Vector3<f32>, Instant)>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Rest { Moving, AtRest, RecentlyAtRest }

#[derive(Debug, Clone, Serialize)]
pub struct Diagnostics {
    pub correction_deg: f32,
    pub rest: Rest,
    pub mode: &'static str,
    pub rest_evidence: &'static str,
    pub unlock_reason: &'static str,
    pub rotation_age_ms: Option<f32>,
    pub acceleration_age_ms: Option<f32>,
    pub pose: Option<PoseKind>,
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
    motion: VecDeque<(Instant, Vector3<f32>)>,
    lock_start_correction: f32,
    blocked: Option<&'static str>,
    unlock_reason: &'static str,
    rotation_age_ms: Option<f32>,
    acceleration_age_ms: Option<f32>,
}
impl State {
    fn new(s: &Sample) -> Self {
        Self { revision: s.revision, position: s.position, last_sample: s.at, previous: s.rotation,
            anchor: s.rotation, anchor_since: s.at, rest: Rest::Moving, recent_since: s.at,
            locked: None, correction: 0.0, mode: "warming_up", motion:VecDeque::new(),
            lock_start_correction:0.0, blocked:None, unlock_reason:"none",
            rotation_age_ms:None, acceleration_age_ms:None }
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

    fn release(&mut self, s: &Sample, reason: &'static str) {
        if self.rest == Rest::AtRest {
            self.rest = Rest::RecentlyAtRest;
            self.recent_since = s.at;
        }
        self.locked = None;
        self.anchor = s.rotation;
        self.anchor_since = s.at;
        self.motion.clear();
        self.unlock_reason = reason;
    }

    fn observe_guarded(&mut self, s: &Sample, now: Instant, anchors_ready: bool) -> f32 {
        self.blocked = None;
        self.rotation_age_ms = Some(now.saturating_duration_since(s.at).as_secs_f32() * 1000.0);
        self.acceleration_age_ms = s.acceleration.map(|(_, at)| now.saturating_duration_since(at).as_secs_f32() * 1000.0);
        let fresh_accel = s.acceleration.filter(|(a, at)| *at <= now &&
            now.duration_since(*at).as_secs_f32() <= FRESH_SECS && a.iter().all(|v| v.is_finite()));
        let reason = if !anchors_ready { Some("anchors_unverified") }
            else if fresh_accel.is_none() { Some("acceleration_unavailable") } else { None };
        if let Some(reason) = reason {
            self.release(s, reason);
            self.previous = s.rotation; self.last_sample = s.at;
            self.blocked = Some(reason);
            return 0.0;
        }
        let (accel, accel_at) = fresh_accel.unwrap();
        let dt = s.at.saturating_duration_since(self.last_sample).as_secs_f32();
        let speed = if dt > 0.0 { self.previous.angle_to(&s.rotation) / dt } else { 0.0 };
        if dt > 0.0 { self.previous = s.rotation; self.last_sample = s.at; }
        if self.rest == Rest::RecentlyAtRest && s.at.saturating_duration_since(self.recent_since).as_secs_f32() >= RECENT_SECS {
            self.rest = Rest::Moving;
        }
        if let Some((at, _)) = self.motion.back() {
            if accel_at < *at || accel_at.saturating_duration_since(*at).as_secs_f32() > FRESH_SECS {
                self.release(s, "acceleration_gap");
            }
        }
        if self.motion.back().is_none_or(|(at, _)| accel_at > *at) { self.motion.push_back((accel_at, accel)); }
        while self.motion.len() > 2 && accel_at.saturating_duration_since(self.motion[1].0).as_secs_f32() >= REST_SECS {
            self.motion.pop_front();
        }
        // Bound storage even for unusually high-rate sources; insufficient time
        // coverage simply prevents a lock rather than treating duplicates as dwell.
        while self.motion.len() > 128 { self.motion.pop_front(); }
        let mean = self.motion.iter().fold(Vector3::zeros(), |sum, (_, a)| sum + a) / self.motion.len() as f32;
        let variance = self.motion.iter().map(|(_, a)| (a - mean).norm_squared()).sum::<f32>() / self.motion.len() as f32;
        let resting = self.rest == Rest::AtRest;
        let tilt = (self.anchor.inverse() * Vector3::y()).angle(&(s.rotation.inverse() * Vector3::y()));
        let error = self.locked.map(|target| locked_error(yaw(self.correction) * s.rotation, target).abs()).unwrap_or(0.0);
        let reason = if accel.norm() > if resting { REST_ACCEL_EXIT } else { REST_ACCEL_ENTER } { Some("acceleration_motion") }
            else if variance > REST_ACCEL_VARIANCE { Some("acceleration_variance") }
            else if speed > if resting { REST_SPEED_EXIT } else { REST_SPEED_ENTER } { Some("rotation_motion") }
            else if tilt > 1f32.to_radians() { Some("tilt_motion") }
            else if error > REST_MAX_ERROR { Some("yaw_discontinuity") }
            else if resting && wrap(self.correction - self.lock_start_correction).abs() >= REST_MAX_CORRECTION - 1e-6 { Some("correction_limit") }
            else { None };
        if let Some(reason) = reason {
            self.release(s, reason);
            return if speed > 30f32.to_radians() { 0.0 } else { dt.min(0.1) };
        }
        let covered = self.motion.len() >= 4 && accel_at.saturating_duration_since(self.motion[0].0).as_secs_f32() >= REST_SECS;
        if self.rest == Rest::Moving && covered && s.at.saturating_duration_since(self.anchor_since).as_secs_f32() >= REST_SECS {
            self.rest = Rest::AtRest;
            self.locked = Some(yaw(self.correction) * s.rotation);
            self.lock_start_correction = self.correction;
            self.unlock_reason = "none";
        }
        dt.min(0.1)
    }
}

#[derive(Default)]
pub struct StayAligned {
    settings: Settings,
    states: HashMap<TrackerId, State>,
    samples: Vec<Sample>,
    anchors_ready: bool,
    pose_candidate: Option<(PoseKind, Instant)>,
    active_pose: Option<PoseKind>,
}
impl StayAligned {
    pub fn set_anchor_reference(&mut self, ready: bool) { self.anchors_ready = ready; }
    pub fn configure(&mut self, settings: Settings) {
        if self.settings != settings { self.clear(); }
        self.settings = settings;
    }
    pub fn clear(&mut self) { self.states.clear(); self.samples.clear(); self.pose_candidate=None; self.active_pose=None; }
    pub fn invalidate(&mut self, id: TrackerId) { self.states.remove(&id); self.samples.retain(|s| s.id != id); }
    pub fn correction(&self, id: TrackerId, revision: u64) -> f32 {
        if !self.settings.enabled { return 0.0; }
        self.states.get(&id).filter(|s| s.revision == revision).map(|s| s.correction).unwrap_or(0.0)
    }
    pub fn diagnostics(&self, id: TrackerId, revision: u64) -> Option<Diagnostics> {
        self.states.get(&id).filter(|s| s.revision == revision).map(|s| Diagnostics {
            correction_deg: s.correction.to_degrees(), rest: s.rest, mode: s.mode,
            rest_evidence: if self.settings.anchor_rest_lock { "anchor_acceleration_heuristic" } else { "orientation_heuristic" },
            unlock_reason:s.unlock_reason, rotation_age_ms:s.rotation_age_ms, acceleration_age_ms:s.acceleration_age_ms,
            pose:self.active_pose,
        })
    }
    /// Pause discards observations/locks, retaining the already applied offset.
    pub fn suspend(&mut self, now: Instant) {
        self.samples.clear();
        self.pose_candidate=None; self.active_pose=None;
        for state in self.states.values_mut() {
            state.anchor_since = now;
            state.last_sample = now;
            state.anchor = state.previous;
            state.rest = Rest::Moving;
            state.locked = None;
            state.mode = "paused";
            state.motion.clear();
            state.unlock_reason = "reference_or_tracking_paused";
            state.rotation_age_ms = None; state.acceleration_age_ms = None;
        }
    }
    /// Change the reference frame without changing any learned relative yaw.
    /// Caller accepts yaw-only frame changes, so correction angles are invariant.
    pub fn rebase(&mut self, rotation: UnitQuaternion<f32>) {
        for state in self.states.values_mut() {
            state.previous = rotation * state.previous;
            state.anchor = rotation * state.anchor;
            state.locked = state.locked.map(|q| rotation * q);
        }
        for sample in &mut self.samples { sample.rotation = rotation * sample.rotation; }
    }
    pub fn update(&mut self, mut samples: Vec<Sample>, hmd: Option<UnitQuaternion<f32>>, now: Instant) {
        samples.retain(|s| s.at <= now && now.saturating_duration_since(s.at).as_secs_f32() <= FRESH_SECS
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
            if state.revision != s.revision || state.position != s.position {
                *state = State::new(s);
            } else if s.at.saturating_duration_since(state.last_sample).as_secs_f32() > FRESH_SECS {
                let held = state.correction;
                *state = State::new(s);
                if self.settings.anchor_rest_lock { state.correction = held; state.unlock_reason = "rotation_gap"; }
            }
            steps.insert(s.id, if self.settings.anchor_rest_lock { state.observe_guarded(s, now, self.anchors_ready) } else { state.observe(s) });
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
                state.motion.clear();
                state.unlock_reason = "rotation_stale_or_ambiguous";
                state.rotation_age_ms = Some(now.saturating_duration_since(state.last_sample).as_secs_f32() * 1000.0);
            }
        }
        // Freeze a common frame for all corrections, avoiding iteration-order bias.
        let frame: Vec<_> = samples.iter().map(|s| Sample {
            rotation: yaw(self.correction(s.id, s.revision)) * s.rotation, ..*s
        }).collect();
        let detected = classify(&frame);
        if detected != self.pose_candidate.map(|(p,_)|p) {
            self.pose_candidate=detected.map(|p|(p,now));
        }
        // Never pull a transitioning/ambiguous posture toward the previous pose.
        self.active_pose=self.pose_candidate.filter(|(_,at)|now.saturating_duration_since(*at).as_secs_f32()>=0.5).map(|(p,_)|p);
        let pose = self.active_pose.and_then(|p| self.settings.pose(p));
        let prone = matches!(self.active_pose,Some(PoseKind::Prone | PoseKind::ProneBent));
        let center = center_yaw(&frame, if prone {None} else {hmd});
        for (s, adjusted) in samples.iter().zip(&frame) {
            let state = self.states.get_mut(&s.id).unwrap();
            if !self.settings.enabled { continue; }
            if self.settings.anchor_rest_lock {
                if let Some(reason) = state.blocked { state.mode = reason; continue; }
            }
            let step = self.settings.rate_deg_per_sec.to_radians() * steps[&s.id];
            let delta = match state.rest {
                Rest::AtRest => {
                    state.mode = if self.settings.anchor_rest_lock { "anchor_rest_lock" } else { "rest_lock" };
                    let bound = if self.settings.anchor_rest_lock {
                        step.min((REST_MAX_CORRECTION - wrap(state.correction - state.lock_start_correction).abs()).max(0.0))
                    } else { step };
                    state.locked.map(|target| locked_error(adjusted.rotation, target)).unwrap_or(0.0).clamp(-bound, bound)
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
            PoseKind::Kneeling => "High kneeling pose not detected. Kneel with your torso and thighs upright and both shins flat behind you",
            PoseKind::Heels => "Sitting-on-heels pose not detected. Sit back on both heels with your torso upright",
            PoseKind::Prone => "Belly-down pose not detected. Lie on your belly with both legs extended",
            PoseKind::ProneBent => "Belly-down bent-knees pose not detected. Lie on your belly with both knees bent and shins pointing upward",
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
    else if frame.iter().filter(|s|center_weight(s.position)>0.0).any(|s| {
        let q=s.rotation;
        [(q*Vector3::x()).y.abs(),(q*Vector3::y()).y.abs(),(q*Vector3::z()).y.abs()].into_iter().fold(0.0f32,f32::max)<0.75
    }) { None }
    else if torso.iter().all(|p|*p==Up) && thighs==[Up;2] && shins==[FrontDown;2] {Some(PoseKind::Kneeling)}
    else if torso.iter().all(|p|*p==Up) && thighs==[FrontUp;2] && shins==[FrontDown;2] {Some(PoseKind::Heels)}
    else if torso.iter().all(|p|*p==FrontDown) && thighs==[FrontDown;2] && shins==[FrontDown;2] {Some(PoseKind::Prone)}
    else if torso.iter().all(|p|*p==FrontDown) && thighs==[FrontDown;2] && shins==[Down;2] {Some(PoseKind::ProneBent)}
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
        Sample { id: TrackerId::new([position; 6], 0), position, rotation: q, at, revision: 1, acceleration:None }
    }
    fn body(now: Instant, q: UnitQuaternion<f32>) -> Vec<Sample> {
        [4, 6, 7, 8, 9, 10].map(|p| sample(p, q, now)).to_vec()
    }
    fn pitch(deg: f32) -> UnitQuaternion<f32> { UnitQuaternion::from_axis_angle(&Vector3::x_axis(), deg.to_radians()) }

    fn guarded() -> StayAligned {
        let mut sa = configured();
        sa.configure(Settings {anchor_rest_lock:true, ..sa.settings});
        sa.set_anchor_reference(true);
        sa
    }
    fn quiet_body(now: Instant, q: UnitQuaternion<f32>) -> Vec<Sample> {
        body(now, q).into_iter().map(|s| Sample {acceleration:Some((Vector3::zeros(), now)), ..s}).collect()
    }

    #[test]
    fn pose_transitions_reject_asymmetry_and_prone_ignores_head_yaw() {
        let start=Instant::now();
        let mut sa=configured();
        sa.configure(Settings {prone:Some(ZERO), ..sa.settings});
        let mut other=configured(); other.configure(sa.settings);
        for i in 0..25 {
            let now=at(start,i as f32/30.0);
            let frame=body(now,pitch(-90.0));
            sa.update(frame.clone(),Some(yaw(1.0)),now);
            other.update(frame,Some(yaw(-1.0)),now);
            assert_eq!(sa.active_pose,if i>=15 {Some(PoseKind::Prone)} else {None});
        }
        for (id,state) in &sa.states {assert!((state.correction-other.states[id].correction).abs()<1e-6);}
        let now=at(start,0.9);
        let mut frame=body(now,pitch(-90.0));
        frame.iter_mut().find(|s|s.position==9).unwrap().rotation=pitch(180.0);
        assert_eq!(classify(&frame),None);
        sa.update(frame,None,now); assert_eq!(sa.active_pose,None);
        let mut frame=body(now,UnitQuaternion::identity());
        for s in frame.iter_mut().filter(|s|matches!(s.position,9|10)) {s.rotation=pitch(-46.0);}
        assert_eq!(classify(&frame),None);
        frame.retain(|s|s.position!=7); assert_eq!(classify(&frame),None);
        let old:Settings=serde_json::from_str(r#"{"enabled":false,"rate_deg_per_sec":0.2}"#).unwrap();
        assert!(old.prone.is_none() && !old.anchor_rest_lock);
    }

    #[test]
    fn guarded_rest_counters_signed_drift_beyond_legacy_range_in_prone_and_side_poses() {
        for hz in [30, 90] {
            for sign in [-1.0f32, 1.0] {
                for pose in [pitch(-90.0), UnitQuaternion::from_axis_angle(&Vector3::z_axis(), 90f32.to_radians())] {
                    let mut sa = guarded(); let start = Instant::now();
                    for i in 0..=30*hz {
                        let t = i as f32 / hz as f32; let now = at(start, t);
                        sa.update(quiet_body(now, yaw((sign*t*0.1).to_radians()) * pose), None, now);
                    }
                    for state in sa.states.values() {
                        assert_eq!(state.mode, "anchor_rest_lock");
                        assert!((state.correction.to_degrees() + sign*2.9).abs() < 0.1);
                        assert!(state.locked.is_some());
                        let measured = yaw((sign*3.0).to_radians()) * pose;
                        assert!(((yaw(state.correction)*measured).inverse()*Vector3::y() - measured.inverse()*Vector3::y()).norm() < 1e-5);
                    }
                }
            }
        }
    }

    #[test]
    fn guarded_lock_releases_on_motion_uncertainty_and_stale_data_without_undoing_correction() {
        for case in 0..7 {
            let mut sa = guarded(); let start = Instant::now();
            for i in 0..300 {
                let t=i as f32/30.0; let now=at(start,t);
                sa.update(quiet_body(now,yaw((t*0.1).to_radians())*pitch(-90.0)),None,now);
            }
            let id=TrackerId::new([6;6],0); let held=sa.correction(id,1);
            assert!(held.abs()>0.01);
            let now=at(start,10.0);
            let mut frame=quiet_body(now,yaw(1f32.to_radians())*pitch(-90.0));
            match case {
                0=>sa.set_anchor_reference(false),
                1=>for s in &mut frame {s.acceleration=None;},
                2=>for s in &mut frame {s.acceleration=Some((Vector3::zeros(),start));},
                3=>for s in &mut frame {s.acceleration=Some((Vector3::new(1.0,0.0,0.0),now));},
                4=>for s in &mut frame {s.rotation=yaw(0.5)*pitch(-90.0);},
                5=>for s in &mut frame {s.acceleration=Some((Vector3::new(f32::NAN,0.0,0.0),now));},
                _=>for s in &mut frame {s.at=start;},
            }
            sa.update(frame,None,now);
            assert_ne!(sa.states[&id].rest,Rest::AtRest,"case {case}");
            assert!(sa.states[&id].locked.is_none());
            assert!((sa.correction(id,1)-held).abs()<1e-6,"case {case}");
            // No stale offsets disappear when a fresh stream returns.
            let now=at(start,10.5);
            sa.update(quiet_body(now,yaw(1f32.to_radians())*pitch(-90.0)),None,now);
            assert!((sa.correction(id,1)-held).abs()<1e-6);
        }
    }

    #[test]
    fn guarded_rest_needs_distinct_fresh_acceleration_samples_and_dwell() {
        for case in 0..4 {
            let mut sa=guarded(); let start=Instant::now();
            for i in 0..120 {
                let now=at(start,i as f32/30.0);
                let mut frame=quiet_body(now,pitch(-90.0));
                for s in &mut frame {s.acceleration=match case {
                    0=>Some((Vector3::zeros(),start)),
                    1=>Some((Vector3::zeros(),at(start,10.0))),
                    2=>Some((Vector3::new(0.0,9.81,0.0),now)),
                    _=>Some((Vector3::new(if i%2==0 {0.14} else {-0.14},0.0,0.0),now)),
                };}
                sa.update(frame,None,now);
                assert!(sa.states.values().all(|s|s.rest!=Rest::AtRest));
            }
        }
    }

    #[test]
    fn guarded_rest_bounds_correction_and_preserves_offsets_through_recovery() {
        let mut sa=guarded(); let start=Instant::now(); let mut reached_limit=false;
        for i in 0..2400 {
            let t=i as f32/30.0; let now=at(start,t);
            sa.update(quiet_body(now,yaw((t*0.1).to_radians())*pitch(-90.0)),None,now);
            for s in sa.states.values() {
                if s.rest==Rest::AtRest {assert!(wrap(s.correction-s.lock_start_correction).abs()<=REST_MAX_CORRECTION+1e-5);}
                reached_limit |= s.unlock_reason=="correction_limit";
            }
        }
        assert!(reached_limit);
        let id=TrackerId::new([6;6],0); let held=sa.correction(id,1);
        sa.suspend(at(start,80.0)); sa.rebase(yaw(0.4));
        sa.set_anchor_reference(false);
        sa.update(quiet_body(at(start,80.03),yaw(0.4+8f32.to_radians())*pitch(-90.0)),None,at(start,80.03));
        assert_eq!(sa.correction(id,1),held);
        assert!(sa.states[&id].locked.is_none());
        sa.set_anchor_reference(true);
        for i in 1..60 {
            let now=at(start,80.03+i as f32/30.0);
            sa.update(quiet_body(now,yaw(0.4+8f32.to_radians())*pitch(-90.0)),None,now);
        }
        assert_eq!(sa.states[&id].mode,"anchor_rest_lock");
        assert!((sa.correction(id,1)-held).abs()<1e-5);
    }

    #[test]
    fn reference_rebase_preserves_offsets_and_rotates_all_orientation_targets() {
        let start = Instant::now();
        let mut sa = configured();
        for i in 0..300 {
            let t = i as f32 / 30.0;
            sa.update(body(at(start, t), yaw((t * 0.05).to_radians())), None, at(start, t));
        }
        let id = TrackerId::new([6; 6], 0);
        let previous = sa.states[&id].previous;
        let locked = sa.states[&id].locked.unwrap();
        let correction = sa.correction(id, 1);
        assert!(correction.abs() > 0.001);
        let delta = yaw(0.4);
        sa.rebase(delta);
        assert_eq!(sa.correction(id, 1), correction);
        assert!(sa.states[&id].previous.angle_to(&(delta * previous)) < 1e-6);
        assert!(sa.states[&id].locked.unwrap().angle_to(&(delta * locked)) < 1e-6);
        assert!(sa.samples.iter().all(|s| s.rotation.angle_to(&(delta * previous)) < 1e-6));
        sa.suspend(at(start, 10.0));
        sa.rebase(delta.inverse());
        assert_eq!(sa.correction(id, 1), correction);
        assert!(sa.states[&id].locked.is_none());
        let now = at(start, 10.03);
        sa.update(body(now, previous), None, now);
        assert_eq!(sa.correction(id, 1), correction);
    }

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
        for kind in [PoseKind::Standing, PoseKind::Sitting, PoseKind::Flat, PoseKind::Kneeling, PoseKind::Heels, PoseKind::Prone, PoseKind::ProneBent] {
            let mut sa = StayAligned::default();
            let mut frame = body(start, UnitQuaternion::identity());
            for s in &mut frame {
                let tilt = match kind {
                    PoseKind::Standing => 0.0,
                    PoseKind::Sitting if matches!(s.position, 7 | 8) => 90.0,
                    PoseKind::Sitting => 0.0,
                    PoseKind::Flat => 90.0,
                    PoseKind::Kneeling => if matches!(s.position, 9 | 10) {-90.0} else {0.0},
                    PoseKind::Heels => match s.position {7 | 8=>90.0,9 | 10=>-90.0,_=>0.0},
                    PoseKind::Prone => -90.0,
                    PoseKind::ProneBent => if matches!(s.position,9 | 10) {180.0} else {-90.0},
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
