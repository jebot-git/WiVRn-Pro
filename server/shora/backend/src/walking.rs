//! Gait recognition uses real, calibrated leg sensors, never inferred foot presence.
use crate::{
    calibration::Calibration,
    feeder::FreshHmdHandle,
    tracker::{SensorStatus, Tracker},
};
use nalgebra::Vector3;
use serde::{Deserialize, Serialize};
use std::time::{Duration, Instant};

#[derive(Debug, Clone, Copy, PartialEq, Serialize, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Settings {
    pub lift_deg: f32,
    pub sensitivity: f32,
    pub max_stick: f32,
    pub stop_ms: u32,
    pub turn_deadzone_deg: f32,
    pub turn_range_deg: f32,
    pub max_turn: f32,
}
impl Default for Settings {
    fn default() -> Self {
        Self {
            lift_deg: 12.,
            sensitivity: 1.,
            max_stick: 0.8,
            stop_ms: 350,
            turn_deadzone_deg: 8.,
            turn_range_deg: 35.,
            max_turn: 0.65,
        }
    }
}
impl Settings {
    pub fn validate(&self) -> anyhow::Result<()> {
        for (v, lo, hi) in [
            (self.lift_deg, 6., 35.),
            (self.sensitivity, 0.2, 3.),
            (self.max_stick, 0.1, 1.),
            (self.turn_deadzone_deg, 3., 25.),
            (self.turn_range_deg, 15., 90.),
            (self.max_turn, 0.1, 1.),
        ] {
            anyhow::ensure!(
                v.is_finite() && (lo..=hi).contains(&v),
                "Walking setting out of range"
            );
        }
        anyhow::ensure!(
            (150..=600).contains(&self.stop_ms),
            "Walking stop_ms must be 150–600"
        );
        anyhow::ensure!(
            self.turn_range_deg > self.turn_deadzone_deg + 5.,
            "Turn range must exceed deadzone by 5 degrees"
        );
        Ok(())
    }
}
#[derive(Clone, Copy, Debug)]
pub struct Sample {
    pub time: f64,
    pub revision: u64,
    pub pitch: [f32; 2],
    pub shortening: [f32; 2],
    pub head: [f32; 3],
    pub torso: [Option<f32>; 2], // hip/chest yaw in radians
}
#[derive(Clone, Copy, Default, Serialize, Debug)]
pub struct Output {
    pub valid: bool,
    pub moving: bool,
    pub turn_available: bool,
    pub forward: f32,
    pub turn: f32,
    pub steps: u64,
}
#[derive(Default)]
pub struct Detector {
    revision: Option<u64>,
    last: Option<Sample>,
    standing_since: Option<f64>,
    baseline: Option<[f32; 2]>,
    raised: [bool; 2],
    peaks: [f32; 2],
    last_step: Option<(usize, f64)>,
    last_motion: Option<f64>,
    motion_pitch: [f32; 2],
    motion_shortening: [f32; 2],
    steps: u64,
    verified_steps: u64,
    alternating: u32,
    intervals: Vec<f64>,
    speed: f32,
    torso_zero: [Option<f32>; 2],
    torso_mask: u8,
    turn: f32,
    calibrating: Option<(f64, u64, f32)>,
    pub message: String,
}
fn angle(a: f32) -> f32 {
    (a + std::f32::consts::PI).rem_euclid(std::f32::consts::TAU) - std::f32::consts::PI
}
impl Detector {
    pub fn reset(&mut self) {
        *self = Self::default();
    }
    pub fn calibrate(&mut self) {
        self.reset();
        self.message = "Stand still for two seconds, then march for eight seconds".into();
    }
    pub fn update(&mut self, sample: Option<Sample>, settings: Settings) -> Output {
        let Some(s) = sample else {
            self.reset();
            self.message = "Waiting for fresh calibrated thigh/shin sensors and HMD".into();
            return Output::default();
        };
        if !s.time.is_finite()
            || s.pitch
                .iter()
                .chain(s.shortening.iter())
                .chain(s.head.iter())
                .any(|v| !v.is_finite())
            || s.torso.iter().flatten().any(|v| !v.is_finite())
        {
            self.reset();
            return Output::default();
        }
        if self.revision != Some(s.revision)
            || self
                .last
                .is_some_and(|p| s.time <= p.time || s.time - p.time > 0.15)
        {
            self.reset();
            self.revision = Some(s.revision);
        }
        let dt = self
            .last
            .map_or(0.033, |p| (s.time - p.time) as f32)
            .clamp(0.001, 0.15);
        let old = self.last.replace(s);
        // Reject real walking and tracking-origin discontinuities. Relative leg
        // angles independently reject HMD bobbing from the gait signal.
        let translating =
            old.is_some_and(|p| ((s.head[0] - p.head[0]).hypot(s.head[2] - p.head[2])) / dt > 0.45);
        if translating {
            self.standing_since = None;
            self.last_step = None;
            self.alternating = 0;
            self.raised = [false; 2];
            self.speed = 0.;
            self.turn = 0.;
            return Output {
                valid: true,
                steps: self.steps,
                ..Default::default()
            };
        }
        if self.baseline.is_none() {
            let still = old.is_some_and(|p| (0..2).all(|h| (s.pitch[h] - p.pitch[h]).abs() < 0.7))
                && s.pitch.iter().all(|a| a.abs() < 25.);
            if !still {
                self.standing_since = None;
            } else {
                let start = *self.standing_since.get_or_insert(s.time);
                if s.time - start >= 2. {
                    self.baseline = Some(s.pitch);
                    self.torso_zero = s.torso;
                    self.calibrating = Some((s.time, self.verified_steps, 0.));
                }
            }
            self.message = "Walking setup: stand upright and still for two seconds".into();
            return Output::default();
        }
        let base = self.baseline.unwrap();
        let lift = [s.pitch[0] - base[0], s.pitch[1] - base[1]];
        let threshold = settings.lift_deg;
        let both = lift.iter().all(|v| *v > threshold * 0.75);
        if both {
            self.alternating = 0;
            self.last_step = None;
            self.raised = [false; 2];
            self.speed = 0.;
        }
        let mut stepped = None;
        if !both {
            for h in 0..2 {
                if !self.raised[h] && lift[h] > threshold && s.shortening[h] > 0.008 {
                    self.raised[h] = true;
                    self.peaks[h] = lift[h];
                }
                if self.raised[h] {
                    self.peaks[h] = self.peaks[h].max(lift[h]);
                    if lift[h] < threshold * 0.4 {
                        self.raised[h] = false;
                        stepped = Some(h);
                    }
                }
            }
        }
        if let Some(h) = stepped {
            let interval = self.last_step.map(|(_, t)| s.time - t);
            let alternating = self
                .last_step
                .is_some_and(|(prev, t)| prev != h && (0.18..=1.2).contains(&(s.time - t)));
            if alternating {
                self.alternating += 1;
                self.verified_steps += 1;
                self.intervals.push(interval.unwrap());
                if self.intervals.len() > 4 {
                    self.intervals.remove(0);
                }
            } else {
                self.alternating = 1;
                self.intervals.clear();
            }
            self.steps += 1;
            self.last_step = Some((h, s.time));
        }
        let since = self.last_step.map_or(f64::INFINITY, |(_, t)| s.time - t);
        // Require a meaningful change in leg angle AND shortening, rather
        // than differentiating sensor jitter. A held knee is not locomotion.
        for h in 0..2 {
            if (s.pitch[h] - self.motion_pitch[h]).abs() >= 3.
                && (s.shortening[h] - self.motion_shortening[h]).abs() >= 0.003
            {
                self.last_motion = Some(s.time);
                self.motion_pitch[h] = s.pitch[h];
                self.motion_shortening[h] = s.shortening[h];
            }
        }
        let physical_motion = self.last_motion.is_some_and(|t| s.time - t < 0.12);
        let active = !both && self.alternating >= 2 && physical_motion && since < 1.2;
        let cadence = if self.intervals.is_empty() {
            0.
        } else {
            self.intervals.len() as f32 / self.intervals.iter().sum::<f64>() as f32
        };
        let target = if active {
            (cadence * 0.3 * settings.sensitivity).min(settings.max_stick)
        } else {
            0.
        };
        let blend = if target > self.speed {
            dt / (0.1 + dt)
        } else {
            dt / (settings.stop_ms as f32 / 3000. + dt)
        };
        self.speed += (target - self.speed) * blend;
        if since > settings.stop_ms as f64 / 1000. && !self.raised.iter().any(|v| *v) {
            self.speed = 0.;
        }
        if self.speed < 0.01 {
            self.speed = 0.;
        }
        // Neutral headings follow slow drift ONLY while not stepping. Trackers
        // appearing/disappearing recapture neutral instead of generating a jump.
        let mask = u8::from(s.torso[0].is_some()) | (u8::from(s.torso[1].is_some()) << 1);
        if mask != self.torso_mask {
            self.torso_zero = s.torso;
            self.torso_mask = mask;
            self.turn = 0.;
        }
        if !active {
            for h in 0..2 {
                if let Some(yaw) = s.torso[h] {
                    let zero = self.torso_zero[h].get_or_insert(yaw);
                    *zero += angle(yaw - *zero) * (dt / (0.3 + dt));
                }
            }
        }
        let mut twist = 0.;
        let mut weight = 0.;
        for (h, w) in [(0, 0.7), (1, 0.3)] {
            if let (Some(yaw), Some(zero)) = (s.torso[h], self.torso_zero[h]) {
                twist += angle(yaw - zero) * w;
                weight += w;
            }
        }
        let degrees = if weight > 0. {
            twist / weight * 180. / std::f32::consts::PI
        } else {
            0.
        };
        let turn_target = if active {
            -degrees.signum()
                * ((degrees.abs() - settings.turn_deadzone_deg)
                    / (settings.turn_range_deg - settings.turn_deadzone_deg))
                    .clamp(0., 1.)
                * settings.max_turn
        } else {
            0.
        };
        self.turn = if active {
            self.turn + (turn_target - self.turn) * (dt / (0.1 + dt))
        } else {
            0.
        };
        // Setup collects a standing baseline then verifies a short marching sample;
        // it never emits locomotion. Explicit setup and first use share this path.
        if let Some((start, steps, peak)) = &mut self.calibrating {
            *peak = peak.max(lift[0].max(lift[1]));
            self.message = "Walking setup: march on the spot for eight seconds".into();
            if s.time - *start >= 8. {
                if self.verified_steps - *steps >= 4 && *peak >= threshold {
                    self.calibrating = None;
                    self.message = "Walking ready".into();
                } else {
                    self.baseline = None;
                    self.calibrating = None;
                    self.standing_since = None;
                    self.message =
                        "Walking setup: no alternating gait; stand still and retry".into();
                }
                self.alternating = 0;
                self.speed = 0.;
                self.turn = 0.;
            }
            return Output {
                steps: self.steps,
                ..Default::default()
            };
        }
        self.message = if active {
            "Walking"
        } else {
            "Walking ready · standing"
        }
        .into();
        Output {
            valid: true,
            moving: active,
            turn_available: mask != 0,
            forward: self.speed,
            turn: self.turn,
            steps: self.steps,
        }
    }
}

/// Sample actual calibrated sensors before prediction/smoothing. Never treat an
/// FK-generated ankle/foot as proof of a live sensor. Duplicate roles fail closed.
pub fn sample(
    trackers: &[Tracker],
    calibration: &Calibration,
    hmd: &FreshHmdHandle,
    now: Instant,
    time: f64,
    revision: u64,
) -> Option<Sample> {
    if calibration.reference_needs_reset() {
        return None;
    }
    let head = hmd.read().ok()?.as_ref().copied()?;
    if now.saturating_duration_since(head.at) > Duration::from_millis(150) {
        return None;
    }
    let rotation = |role| {
        let mut matches = trackers.iter().filter(|t| t.position == role);
        let t = matches.next()?;
        if matches.next().is_some()
            || t.status != SensorStatus::Ok
            || !calibration.is_calibrated(t.id())
            || now.saturating_duration_since(t.rotation_updated_at?) > Duration::from_millis(150)
        {
            return None;
        }
        Some(calibration.adjust(t.id(), t.rotation?))
    };
    let mut pitch = [0.; 2];
    let mut shortening = [0.; 2];
    for h in 0..2 {
        let thigh = rotation(7 + h as u8)? * Vector3::new(0., -1., 0.);
        let shin = rotation(9 + h as u8)? * Vector3::new(0., -1., 0.);
        pitch[h] = (-thigh.y).clamp(-1., 1.).acos().to_degrees();
        shortening[h] = 0.4 * (1. + thigh.y) + 0.4 * (1. + shin.y);
        if thigh.y > -0.5 || shin.y > -0.1 {
            return None;
        }
    }
    let yaw = |role| {
        rotation(role).and_then(|q| {
            let f = q * Vector3::new(0., 0., -1.);
            (f.x.hypot(f.z) > 0.1).then(|| (-f.x).atan2(-f.z))
        })
    };
    Some(Sample {
        time,
        revision,
        pitch,
        shortening,
        head: [head.pose[0], head.pose[1], head.pose[2]],
        torso: [yaw(6), yaw(4)],
    })
}

pub struct Publisher {
    socket: Option<std::os::unix::net::UnixDatagram>,
    path: Option<std::path::PathBuf>,
    generation: u64,
    sequence: u64,
}
pub fn now_ns() -> u64 {
    let mut t: libc::timespec = unsafe { std::mem::zeroed() };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut t) };
    t.tv_sec as u64 * 1_000_000_000 + t.tv_nsec as u64
}
impl Publisher {
    pub fn new() -> Self {
        let socket = std::os::unix::net::UnixDatagram::unbound()
            .ok()
            .filter(|s| s.set_nonblocking(true).is_ok());
        let path = std::env::var_os("XDG_RUNTIME_DIR")
            .map(|p| std::path::PathBuf::from(p).join("wivrn-pro/walking.sock"));
        Self {
            socket,
            path,
            generation: now_ns(),
            sequence: 0,
        }
    }
    pub fn reset(&mut self) {
        self.generation = now_ns();
    }
    pub fn publish(&mut self, output: Output) {
        let (Some(socket), Some(path)) = (&self.socket, &self.path) else {
            return;
        };
        self.sequence += 1;
        let mut packet = [0u8; 48];
        packet[..4].copy_from_slice(b"WVW1");
        let flags = u32::from(output.valid)
            | (u32::from(output.moving) << 1)
            | (u32::from(output.turn_available) << 2);
        packet[4..8].copy_from_slice(&flags.to_le_bytes());
        for (at, value) in [
            (8, now_ns()),
            (16, self.generation),
            (24, self.sequence),
            (32, output.steps),
        ] {
            packet[at..at + 8].copy_from_slice(&value.to_le_bytes());
        }
        packet[40..44].copy_from_slice(&output.forward.to_le_bytes());
        packet[44..48].copy_from_slice(&output.turn.to_le_bytes());
        let _ = socket.send_to(&packet, path);
    }
}
#[cfg(test)]
#[path = "walking_tests.rs"]
mod tests;
