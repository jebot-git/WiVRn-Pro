// SPDX-License-Identifier: GPL-3.0-only
// Gesture/ray algorithm adapted from the local GPL-2.0-or-later KWin
// vrhandpointer.cpp (see fork/PROVENANCE.md). OpenXR coordinates are metres.
use glam::{Affine3A, Mat3, Quat, Vec3};
use openxr as xr;
use std::time::Duration;

#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub enum Gesture {
    #[default]
    Neutral,
    Click,
    Grab,
    RightClick,
}

#[derive(Default)]
pub struct HandInput {
    gesture: Gesture,
    armed: bool,
    neutral_since: Option<Duration>,
    candidate: Gesture,
    candidate_since: Duration,
    last_sample: Option<Duration>,
    runtime_aim: bool,
    ray: Option<Affine3A>,
}

pub struct Sample {
    pub wrist: Affine3A,
    pub ray: Option<Affine3A>,
    pub gesture: Gesture,
}

fn position(p: xr::Vector3f) -> Vec3 {
    Vec3::new(p.x, p.y, p.z)
}
fn pose(p: xr::Posef) -> Option<Affine3A> {
    let q = Quat::from_xyzw(
        p.orientation.x,
        p.orientation.y,
        p.orientation.z,
        p.orientation.w,
    );
    let p = position(p.position);
    (p.is_finite() && q.is_finite() && (0.5..1.5).contains(&q.length_squared()))
        .then(|| Affine3A::from_rotation_translation(q.normalize(), p))
}

impl HandInput {
    pub fn reset(&mut self) {
        *self = Self::default();
    }
    pub fn update(
        &mut self,
        now: Duration,
        joints: Option<&xr::HandJointLocations>,
        native_aim: Option<Affine3A>,
        head: Affine3A,
        left: bool,
    ) -> Option<Sample> {
        let Some(joints) = joints else {
            self.reset();
            return None;
        };
        let result = self.sample(now, joints, native_aim, head, left);
        if result.is_none() {
            self.reset();
        }
        result
    }
    fn sample(
        &mut self,
        now: Duration,
        j: &xr::HandJointLocations,
        native_aim: Option<Affine3A>,
        head: Affine3A,
        left: bool,
    ) -> Option<Sample> {
        let valid = xr::SpaceLocationFlags::POSITION_VALID;
        if !head.is_finite()
            || j.iter().any(|v| {
                !v.location_flags.contains(valid) || !position(v.pose.position).is_finite()
            })
            || !j[0]
                .location_flags
                .contains(xr::SpaceLocationFlags::POSITION_TRACKED)
            || !j[1].location_flags.contains(
                valid
                    | xr::SpaceLocationFlags::ORIENTATION_VALID
                    | xr::SpaceLocationFlags::POSITION_TRACKED,
            )
        {
            return None;
        }
        let wrist = pose(j[1].pose)?;
        let p = |i: usize| position(j[i].pose.position);
        let width = p(7).distance(p(22));
        if !(0.03..=0.13).contains(&width) {
            return None;
        }
        let palm = head.inverse().transform_point3(p(0));
        // Keep the wrist attached even when lowering the hand disables pointing.
        if palm.y < -0.50 || palm.z > -0.12 {
            self.reset();
            return Some(Sample {
                wrist,
                ray: None,
                gesture: Gesture::Neutral,
            });
        }
        let expired = self
            .last_sample
            .is_some_and(|at| now.saturating_sub(at) > Duration::from_millis(150));
        let runtime = native_aim.is_some_and(|a| a.is_finite());
        if expired || runtime != self.runtime_aim {
            self.reset();
        }
        self.runtime_aim = runtime;
        let dt = self.last_sample.map_or(0.05, |at| {
            now.saturating_sub(at).as_secs_f32().clamp(0.001, 0.05)
        });
        self.last_sample = Some(now);
        let target = if let Some(aim) = native_aim.filter(|a| a.is_finite()) {
            aim
        } else {
            let shoulder =
                head.transform_point3(Vec3::new(if left { -0.16 } else { 0.16 }, -0.18, 0.0));
            let direction = p(7) - shoulder;
            if direction.length_squared() < 0.01 {
                return None;
            }
            let z = -direction.normalize();
            let x = head.transform_vector3(Vec3::Y).cross(z).try_normalize()?;
            let y = z.cross(x).normalize();
            Affine3A::from_rotation_translation(Quat::from_mat3(&Mat3::from_cols(x, y, z)), p(7))
        };
        let ray = self.ray.map_or(target, |old| {
            let a = 1.0 - (-dt / 0.035).exp();
            Affine3A::from_rotation_translation(
                Quat::from_affine3(&old)
                    .slerp(Quat::from_affine3(&target), a)
                    .normalize(),
                Vec3::from(old.translation).lerp(target.translation.into(), a),
            )
        });
        self.ray = Some(ray);
        let ratios = [
            p(5).distance(p(10)) / width,
            p(5).distance(p(15)) / width,
            p(5).distance(p(20)) / width,
        ];
        self.update_gesture(now, ratios);
        Some(Sample {
            wrist,
            ray: Some(ray),
            gesture: self.gesture,
        })
    }
    fn update_gesture(&mut self, now: Duration, ratios: [f32; 3]) {
        let gestures = [Gesture::Click, Gesture::Grab, Gesture::RightClick];
        if let Some(i) = gestures.iter().position(|g| *g == self.gesture) {
            if ratios[i] > 0.42 {
                self.gesture = Gesture::Neutral;
                self.armed = false;
                self.neutral_since = None;
            }
            return;
        }
        if !self.armed {
            if !ratios.iter().all(|v| *v > 0.42) {
                self.neutral_since = None;
            } else if let Some(at) = self.neutral_since {
                if now.saturating_sub(at) >= Duration::from_millis(100) {
                    self.armed = true;
                }
            } else {
                self.neutral_since = Some(now);
            }
            return;
        }
        let pressed: Vec<_> = ratios
            .iter()
            .enumerate()
            .filter(|(_, v)| **v < 0.28)
            .map(|(i, _)| gestures[i])
            .collect();
        let candidate = if pressed.len() == 1 {
            pressed[0]
        } else {
            Gesture::Neutral
        };
        if candidate != self.candidate {
            self.candidate = candidate;
            self.candidate_since = now;
        }
        if candidate != Gesture::Neutral
            && now.saturating_sub(self.candidate_since) >= Duration::from_millis(45)
        {
            self.gesture = candidate;
            self.candidate = Gesture::Neutral;
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn ms(n: u64) -> Duration {
        Duration::from_millis(n)
    }
    fn joints() -> xr::HandJointLocations {
        let joint = xr::HandJointLocation {
            location_flags: xr::SpaceLocationFlags::POSITION_VALID
                | xr::SpaceLocationFlags::POSITION_TRACKED
                | xr::SpaceLocationFlags::ORIENTATION_VALID,
            pose: xr::Posef {
                orientation: xr::Quaternionf {
                    x: 0.,
                    y: 0.,
                    z: 0.,
                    w: 1.,
                },
                position: xr::Vector3f {
                    x: 0.,
                    y: -0.2,
                    z: -0.4,
                },
            },
            radius: 0.01,
        };
        let mut j = [joint; 26];
        for (i, x) in [
            (5, -0.10),
            (7, 0.04),
            (22, -0.04),
            (10, 0.04),
            (15, 0.0),
            (20, -0.04),
        ] {
            j[i].pose.position.x = x;
        }
        j
    }
    #[test]
    fn kwin_gestures_debounce_hysteresis_and_neutral_rearm() {
        for (i, g) in [Gesture::Click, Gesture::Grab, Gesture::RightClick]
            .into_iter()
            .enumerate()
        {
            let mut h = HandInput::default();
            h.update_gesture(ms(0), [1.; 3]);
            h.update_gesture(ms(100), [1.; 3]);
            let mut r = [1.; 3];
            r[i] = 0.2;
            h.update_gesture(ms(110), r);
            h.update_gesture(ms(154), r);
            assert_eq!(h.gesture, Gesture::Neutral);
            h.update_gesture(ms(155), r);
            assert_eq!(h.gesture, g);
            r[i] = 0.35;
            h.update_gesture(ms(200), r);
            assert_eq!(h.gesture, g);
            h.update_gesture(ms(210), [1.; 3]);
            assert_eq!(h.gesture, Gesture::Neutral);
            h.update_gesture(ms(260), [0.1; 3]);
            assert_eq!(h.gesture, Gesture::Neutral);
        }
    }
    #[test]
    fn ambiguous_pinch_does_not_press() {
        let mut h = HandInput::default();
        h.update_gesture(ms(0), [1.; 3]);
        h.update_gesture(ms(100), [1.; 3]);
        h.update_gesture(ms(110), [0.1, 0.1, 1.]);
        h.update_gesture(ms(200), [0.1, 0.1, 1.]);
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    #[test]
    fn wrist_independent_of_aim_and_lowered_hand() {
        let mut h = HandInput::default();
        let mut j = joints();
        let s = h
            .update(
                ms(0),
                Some(&j),
                Some(Affine3A::IDENTITY),
                Affine3A::IDENTITY,
                false,
            )
            .unwrap();
        assert_ne!(s.wrist.translation, s.ray.unwrap().translation);
        j[0].pose.position.y = -0.6;
        let s = h
            .update(ms(10), Some(&j), None, Affine3A::IDENTITY, false)
            .unwrap();
        assert!(s.ray.is_none());
        assert_eq!(s.wrist.translation, Vec3::new(0., -0.2, -0.4).into());
    }
    #[test]
    fn estimated_fingers_accepted_invalid_palm_rejected_and_loss_releases() {
        let mut h = HandInput::default();
        let mut j = joints();
        j[10].location_flags = xr::SpaceLocationFlags::POSITION_VALID;
        assert!(
            h.update(ms(0), Some(&j), None, Affine3A::IDENTITY, false)
                .is_some()
        );
        h.gesture = Gesture::Grab;
        j[0].location_flags = xr::SpaceLocationFlags::POSITION_VALID;
        assert!(
            h.update(ms(10), Some(&j), None, Affine3A::IDENTITY, false)
                .is_none()
        );
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    #[test]
    fn timeout_and_ray_switch_require_rearming() {
        let mut h = HandInput::default();
        let j = joints();
        h.update(ms(0), Some(&j), None, Affine3A::IDENTITY, false);
        h.gesture = Gesture::Grab;
        h.armed = true;
        h.update(ms(200), Some(&j), None, Affine3A::IDENTITY, false);
        assert_eq!(h.gesture, Gesture::Neutral);
        assert!(!h.armed);
        h.gesture = Gesture::Click;
        h.update(
            ms(210),
            Some(&j),
            Some(Affine3A::IDENTITY),
            Affine3A::IDENTITY,
            false,
        );
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    #[test]
    fn missing_joints_release_click_and_held_pinch_cannot_reacquire() {
        let mut h = HandInput::default();
        let mut j = joints();
        for t in [0, 100] {
            h.update(ms(t), Some(&j), None, Affine3A::IDENTITY, false);
        }
        j[10].pose.position = j[5].pose.position;
        for t in [110, 155] {
            h.update(ms(t), Some(&j), None, Affine3A::IDENTITY, false);
        }
        assert_eq!(h.gesture, Gesture::Click);
        assert!(
            h.update(ms(160), None, None, Affine3A::IDENTITY, false)
                .is_none()
        );
        assert_eq!(h.gesture, Gesture::Neutral);
        for t in [170, 220, 270] {
            assert_eq!(
                h.update(ms(t), Some(&j), None, Affine3A::IDENTITY, false)
                    .unwrap()
                    .gesture,
                Gesture::Neutral
            );
        }
        j[10].pose.position.x = 0.04;
        for t in [280, 380] {
            h.update(ms(t), Some(&j), None, Affine3A::IDENTITY, false);
        }
        j[10].pose.position = j[5].pose.position;
        for t in [390, 435] {
            h.update(ms(t), Some(&j), None, Affine3A::IDENTITY, false);
        }
        assert_eq!(h.gesture, Gesture::Click);
        j[15].pose.position.x = f32::NAN;
        assert!(
            h.update(ms(440), Some(&j), None, Affine3A::IDENTITY, false)
                .is_none()
        );
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    #[test]
    fn fallback_ray_points_from_shoulder_through_index_and_aim_is_smoothed() {
        let mut h = HandInput::default();
        let j = joints();
        let ray = h
            .update(ms(0), Some(&j), None, Affine3A::IDENTITY, true)
            .unwrap()
            .ray
            .unwrap();
        let expected = (position(j[7].pose.position) - Vec3::new(-0.16, -0.18, 0.0)).normalize();
        assert!(
            ray.transform_vector3(Vec3::NEG_Z)
                .abs_diff_eq(expected, 1e-5)
        );
        h.update(
            ms(10),
            Some(&j),
            Some(Affine3A::IDENTITY),
            Affine3A::IDENTITY,
            true,
        );
        let ray = h
            .update(
                ms(20),
                Some(&j),
                Some(Affine3A::from_translation(Vec3::X)),
                Affine3A::IDENTITY,
                true,
            )
            .unwrap()
            .ray
            .unwrap();
        assert!(ray.translation.x > 0.0 && ray.translation.x < 1.0);
    }
}
