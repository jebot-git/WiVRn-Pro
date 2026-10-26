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
    MiddleClick,
    SpaceDrag,
    Grab,
    RightClick,
}

#[derive(Default)]
pub struct HandInput {
    space_drag_enabled: bool,
    keyboard_active: bool,
    double_tap: crate::hand_double_tap::DoubleTap,
    gesture: Gesture,
    armed: bool,
    neutral_since: Option<Duration>,
    candidate: Gesture,
    candidate_since: Duration,
    last_sample: Option<Duration>,
    ray: Option<Affine3A>,
    grab_ray_from_wrist: Option<Affine3A>,
    last_fingers: Option<Duration>,
    release_since: Option<Duration>,
}

pub struct Sample {
    pub wrist: Affine3A,
    pub ray: Option<Affine3A>,
    pub gesture: Gesture,
    pub reset_space: bool,
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
        let space_drag_enabled = self.space_drag_enabled;
        let keyboard_active = self.keyboard_active;
        *self = Self {
            space_drag_enabled,
            keyboard_active,
            ..Default::default()
        };
    }
    pub fn set_keyboard_active(&mut self, active: bool) {
        self.keyboard_active = active;
    }
    pub fn set_space_drag_enabled(&mut self, enabled: bool) {
        if self.space_drag_enabled != enabled {
            self.reset();
            self.space_drag_enabled = enabled;
        }
    }
    fn moving_space_or_window(&self) -> bool {
        matches!(self.gesture, Gesture::Grab | Gesture::SpaceDrag)
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
        let mut result = self.sample(now, joints, native_aim, head, left);
        if let Some(sample) = result.as_mut() {
            sample.reset_space = self.double_tap.update(
                now,
                sample.gesture == Gesture::SpaceDrag,
                sample.wrist.translation.into(),
            );
        }
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
        if self
            .last_sample
            .is_some_and(|at| now.saturating_sub(at) > Duration::from_millis(150))
        {
            self.reset();
        }
        let fingers_valid = j
            .iter()
            .all(|v| v.location_flags.contains(valid) && position(v.pose.position).is_finite())
            && j[0]
                .location_flags
                .contains(xr::SpaceLocationFlags::POSITION_TRACKED)
            && (0.03..=0.13).contains(&p(7).distance(p(22)));
        if fingers_valid {
            self.last_fingers = Some(now);
        } else if !self.moving_space_or_window()
            || !self
                .last_fingers
                .is_some_and(|at| now.saturating_sub(at) <= Duration::from_millis(120))
        {
            self.reset();
            return Some(Sample {
                wrist,
                ray: None,
                gesture: Gesture::Neutral,
                reset_space: false,
            });
        }
        let palm = head.inverse().transform_point3(p(0));
        // Once grabbed, allow moving below the pointing zone. A fresh tracked
        // wrist is always required; only finger occlusion gets a short grace.
        if !self.keyboard_active
            && !self.moving_space_or_window()
            && (palm.y < -0.50 || palm.z > -0.12)
        {
            self.reset();
            return Some(Sample {
                wrist,
                ray: None,
                gesture: Gesture::Neutral,
                reset_space: false,
            });
        }
        let dt = self.last_sample.map_or(0.05, |at| {
            now.saturating_sub(at).as_secs_f32().clamp(0.001, 0.05)
        });
        self.last_sample = Some(now);
        let target = if let Some(offset) = self.grab_ray_from_wrist {
            wrist * offset
        } else if let Some(aim) = native_aim.filter(|a| a.is_finite()) {
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
        let was_grabbing = self.moving_space_or_window();
        if fingers_valid {
            let width = p(7).distance(p(22));
            let mut ratios = [
                p(5).distance(p(10)) / width,
                p(5).distance(p(15)) / width,
                p(5).distance(p(20)) / width,
            ];
            if self.keyboard_active && let Some(contacts) = crate::hand_keyboard::Contacts::from_joints(j) {
                ratios[..2].copy_from_slice(&contacts.pinch_ratios());
            }
            // Tip-to-knuckle distance / bone-chain length: 1 = straight,
            // close to 0 = curled. Independent of thumb contact and hand size.
            let extension = [7, 12, 17, 22].map(|base| {
                let length = (base..base + 3)
                    .map(|i| p(i).distance(p(i + 1)))
                    .sum::<f32>();
                if length > 0.015 {
                    p(base).distance(p(base + 3)) / length
                } else {
                    1.0
                }
            });
            self.update_gesture(now, ratios, extension);
        } else {
            self.release_since = None;
        }
        if self.moving_space_or_window() && !was_grabbing {
            self.grab_ray_from_wrist = Some(wrist.inverse() * ray);
        } else if !self.moving_space_or_window() {
            self.grab_ray_from_wrist = None;
        }
        Some(Sample {
            wrist,
            ray: Some(ray),
            gesture: self.gesture,
            reset_space: false,
        })
    }
    fn update_gesture(&mut self, now: Duration, ratios: [f32; 3], extension: [f32; 4]) {
        let curled = extension.iter().filter(|v| **v < 0.65).count();
        let open = extension.iter().filter(|v| **v > 0.82).count();
        if self.gesture == Gesture::Grab {
            // Opening two fingers for 100 ms releases. One noisy/occluded
            // finger cannot drop a window, and thumb movement is irrelevant.
            if open >= 2 {
                let at = *self.release_since.get_or_insert(now);
                if now.saturating_sub(at) >= Duration::from_millis(100) {
                    self.finish_gesture();
                }
            } else {
                self.release_since = None;
            }
            return;
        }
        if self.gesture == Gesture::SpaceDrag {
            if ratios[0] > 0.42 || ratios[1] > 0.42 {
                let at = *self.release_since.get_or_insert(now);
                if now.saturating_sub(at) >= Duration::from_millis(60) {
                    self.finish_gesture();
                }
            } else {
                self.release_since = None;
            }
            return;
        }
        let gestures = [Gesture::Click, Gesture::MiddleClick, Gesture::RightClick];
        if let Some(i) = gestures.iter().position(|g| *g == self.gesture) {
            if ratios[i] > 0.42 {
                self.finish_gesture();
            }
            return;
        }
        if !self.armed {
            if open < 3 || !ratios.iter().all(|v| *v > 0.42) {
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
        let mut pinches = ratios.iter().enumerate().filter(|(_, v)| **v < 0.28);
        let first = pinches.next().map(|(i, _)| gestures[i]);
        let candidate = if extension[0] < 0.65 && curled >= 3 {
            Gesture::Grab
        } else if self.keyboard_active {
            // Larger contacts may overlap two fingers: choose the closest pair,
            // never turn a keyboard pinch into the offhand playspace chord.
            ratios.iter().enumerate().filter(|(_, r)| **r < 0.28)
                .min_by(|a, b| a.1.total_cmp(b.1))
                .map_or(Gesture::Neutral, |(i, _)| gestures[i])
        } else if self.space_drag_enabled
            && ratios[0] < 0.28
            && ratios[1] < 0.28
            && ratios[2] > 0.42
        {
            Gesture::SpaceDrag
        } else if pinches.next().is_none() {
            first.unwrap_or_default()
        } else {
            Gesture::Neutral
        };
        if candidate != self.candidate {
            self.candidate = candidate;
            self.candidate_since = now;
        }
        let debounce = if matches!(candidate, Gesture::Grab | Gesture::SpaceDrag) {
            80
        } else if self.space_drag_enabled && !self.keyboard_active {
            120
        } else {
            45
        };
        if candidate != Gesture::Neutral
            && now.saturating_sub(self.candidate_since) >= Duration::from_millis(debounce)
        {
            self.gesture = candidate;
            self.candidate = Gesture::Neutral;
            self.release_since = None;
        }
    }
    fn finish_gesture(&mut self) {
        self.gesture = Gesture::Neutral;
        self.armed = false;
        self.neutral_since = None;
        self.release_since = None;
        self.grab_ray_from_wrist = None;
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
        for base in [7, 12, 17, 22] {
            let x = match base {
                7 => 0.04,
                12 => 0.01,
                17 => -0.015,
                _ => -0.04,
            };
            for k in 0..4 {
                j[base + k].pose.position = xr::Vector3f {
                    x,
                    y: -0.2,
                    z: -0.4 - k as f32 * 0.025,
                };
            }
        }
        j
    }
    #[test]
    fn kwin_gestures_debounce_hysteresis_and_neutral_rearm() {
        for (i, g) in [Gesture::Click, Gesture::MiddleClick, Gesture::RightClick]
            .into_iter()
            .enumerate()
        {
            let mut h = HandInput::default();
            h.update_gesture(ms(0), [1.; 3], [1.; 4]);
            h.update_gesture(ms(100), [1.; 3], [1.; 4]);
            let mut r = [1.; 3];
            r[i] = 0.2;
            h.update_gesture(ms(110), r, [1.; 4]);
            h.update_gesture(ms(154), r, [1.; 4]);
            assert_eq!(h.gesture, Gesture::Neutral);
            h.update_gesture(ms(155), r, [1.; 4]);
            assert_eq!(h.gesture, g);
            r[i] = 0.35;
            h.update_gesture(ms(200), r, [1.; 4]);
            assert_eq!(h.gesture, g);
            h.update_gesture(ms(210), [1.; 3], [1.; 4]);
            assert_eq!(h.gesture, Gesture::Neutral);
            h.update_gesture(ms(260), [0.1; 3], [1.; 4]);
            assert_eq!(h.gesture, Gesture::Neutral);
        }
    }
    #[test]
    fn keyboard_overlapping_contacts_choose_nearest_pair_without_space_drag() {
        for (ratios, expected) in [([0.20, 0.10, 1.], Gesture::MiddleClick), ([0.10, 0.20, 1.], Gesture::Click)] {
            let mut hand = HandInput::default();
            hand.set_space_drag_enabled(true);
            hand.set_keyboard_active(true);
            hand.update_gesture(ms(0), [1.; 3], [1.; 4]);
            hand.update_gesture(ms(100), [1.; 3], [1.; 4]);
            hand.update_gesture(ms(110), ratios, [1.; 4]);
            hand.update_gesture(ms(160), ratios, [1.; 4]);
            assert_eq!(hand.gesture, expected);
            hand.update_gesture(ms(170), [0.35, 0.35, 1.], [1.; 4]);
            assert_eq!(hand.gesture, expected); // release hysteresis
            hand.update_gesture(ms(180), [0.5, 0.5, 1.], [1.; 4]);
            assert_eq!(hand.gesture, Gesture::Neutral);
        }
    }

    #[test]
    fn ambiguous_pinch_does_not_press() {
        let mut h = HandInput::default();
        h.update_gesture(ms(0), [1.; 3], [1.; 4]);
        h.update_gesture(ms(100), [1.; 3], [1.; 4]);
        h.update_gesture(ms(110), [0.1, 0.1, 1.], [1.; 4]);
        h.update_gesture(ms(200), [0.1, 0.1, 1.], [1.; 4]);
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
    fn lowered_hand_can_type_only_while_hovering_keyboard() {
        let mut h = HandInput::default();
        let mut j = joints();
        for joint in &mut j {
            joint.pose.position.y -= 0.45;
        }
        assert!(
            h.update(ms(0), Some(&j), None, Affine3A::IDENTITY, false)
                .unwrap()
                .ray
                .is_none()
        );
        h.set_keyboard_active(true);
        assert!(
            h.update(ms(10), Some(&j), None, Affine3A::IDENTITY, false)
                .unwrap()
                .ray
                .is_some()
        );
        h.update(ms(110), Some(&j), None, Affine3A::IDENTITY, false);
        j[5].pose.position = j[10].pose.position;
        h.update(ms(120), Some(&j), None, Affine3A::IDENTITY, false);
        assert_eq!(
            h.update(ms(170), Some(&j), None, Affine3A::IDENTITY, false)
                .unwrap()
                .gesture,
            Gesture::Click
        );
        h.set_keyboard_active(false);
        let sample = h
            .update(ms(180), Some(&j), None, Affine3A::IDENTITY, false)
            .unwrap();
        assert!(sample.ray.is_none());
        assert_eq!(sample.gesture, Gesture::Neutral);
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
        // Invalid fingers stop input but the valid wrist remains attached.
        assert!(
            h.update(ms(200), Some(&j), None, Affine3A::IDENTITY, false)
                .unwrap()
                .ray
                .is_none()
        );
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    #[test]
    fn timeout_requires_rearming_but_ray_switch_keeps_held_button() {
        let mut h = HandInput::default();
        let j = joints();
        h.update(ms(0), Some(&j), None, Affine3A::IDENTITY, false);
        h.gesture = Gesture::Grab;
        h.armed = true;
        h.update(ms(200), Some(&j), None, Affine3A::IDENTITY, false);
        assert_eq!(h.gesture, Gesture::Neutral);
        assert!(!h.armed);
        h.gesture = Gesture::Click;
        let mut j = j;
        j[10].pose.position = j[5].pose.position;
        h.update(
            ms(210),
            Some(&j),
            Some(Affine3A::IDENTITY),
            Affine3A::IDENTITY,
            false,
        );
        assert_eq!(h.gesture, Gesture::Click);
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
        j[10] = joints()[10];
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
                .unwrap()
                .ray
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
    #[test]
    fn grip_uses_finger_curl_and_has_separate_press_release_hysteresis() {
        let mut h = HandInput::default();
        for t in [0, 100] {
            h.update_gesture(ms(t), [1.; 3], [1.; 4]);
        }
        h.update_gesture(ms(110), [0.1; 3], [0.4, 0.4, 0.4, 1.]);
        h.update_gesture(ms(189), [0.1; 3], [0.4, 0.4, 0.4, 1.]);
        assert_eq!(h.gesture, Gesture::Neutral);
        h.update_gesture(ms(190), [0.1; 3], [0.4, 0.4, 0.4, 1.]);
        assert_eq!(h.gesture, Gesture::Grab); // fist wins over ambiguous thumb contacts
        h.update_gesture(ms(220), [1.; 3], [0.7, 0.7, 0.7, 1.]);
        assert_eq!(h.gesture, Gesture::Grab);
        h.update_gesture(ms(240), [1.; 3], [1.; 4]);
        h.update_gesture(ms(300), [1.; 3], [0.4; 4]); // brief open glitch cannot drop
        h.update_gesture(ms(350), [1.; 3], [1.; 4]);
        h.update_gesture(ms(449), [1.; 3], [1.; 4]);
        assert_eq!(h.gesture, Gesture::Grab);
        h.update_gesture(ms(450), [1.; 3], [1.; 4]);
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    #[test]
    fn extended_index_for_direct_touch_is_not_a_grip() {
        let mut h = HandInput::default();
        for t in [0, 100] {
            h.update_gesture(ms(t), [1.; 3], [1.; 4]);
        }
        for t in [110, 210] {
            h.update_gesture(ms(t), [1.; 3], [1., 0.4, 0.4, 0.4]);
        }
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    fn fist() -> xr::HandJointLocations {
        let mut j = joints();
        for base in [7, 12, 17, 22] {
            for (k, (y, z)) in [
                (-0.2, -0.4),
                (-0.22, -0.43),
                (-0.25, -0.415),
                (-0.25, -0.39),
            ]
            .into_iter()
            .enumerate()
            {
                j[base + k].pose.position.y = y;
                j[base + k].pose.position.z = z;
            }
        }
        j
    }
    fn grabbing() -> (HandInput, xr::HandJointLocations) {
        let mut h = HandInput::default();
        for t in [0, 100] {
            h.update(
                ms(t),
                Some(&joints()),
                Some(Affine3A::IDENTITY),
                Affine3A::IDENTITY,
                false,
            );
        }
        let j = fist();
        for t in [110, 190] {
            h.update(
                ms(t),
                Some(&j),
                Some(Affine3A::IDENTITY),
                Affine3A::IDENTITY,
                false,
            );
        }
        assert_eq!(h.gesture, Gesture::Grab);
        (h, j)
    }
    #[test]
    fn closed_grip_survives_aim_loss_and_brief_finger_occlusion_but_expires() {
        let (mut h, mut j) = grabbing();
        j[1].pose.position.x += 0.1;
        let sample = h
            .update(ms(210), Some(&j), None, Affine3A::IDENTITY, false)
            .unwrap();
        assert_eq!(sample.gesture, Gesture::Grab);
        assert!(sample.ray.unwrap().translation.x > 0.0); // wrist moves the window
        for v in &mut j[2..] {
            v.location_flags = xr::SpaceLocationFlags::EMPTY;
        }
        for t in [250, 300] {
            assert_eq!(
                h.update(ms(t), Some(&j), None, Affine3A::IDENTITY, false)
                    .unwrap()
                    .gesture,
                Gesture::Grab
            );
        }
        assert_eq!(
            h.update(ms(331), Some(&j), None, Affine3A::IDENTITY, false)
                .unwrap()
                .gesture,
            Gesture::Neutral
        );
        assert!(h.grab_ray_from_wrist.is_none());
    }
    #[test]
    fn grip_continues_below_pointing_zone_but_wrist_loss_releases_immediately() {
        let (mut h, mut j) = grabbing();
        j[0].pose.position.y = -0.7;
        assert_eq!(
            h.update(ms(210), Some(&j), None, Affine3A::IDENTITY, false)
                .unwrap()
                .gesture,
            Gesture::Grab
        );
        j[1].location_flags = xr::SpaceLocationFlags::EMPTY;
        assert!(
            h.update(ms(220), Some(&j), None, Affine3A::IDENTITY, false)
                .is_none()
        );
        assert_eq!(h.gesture, Gesture::Neutral);
    }
    #[test]
    fn offhand_chord_waits_for_both_pinches_and_releases_without_clicking() {
        let mut h = HandInput::default();
        h.set_space_drag_enabled(true);
        for t in [0, 100] {
            h.update_gesture(ms(t), [1.; 3], [1.; 4]);
        }
        h.update_gesture(ms(110), [0.2, 1., 1.], [1.; 4]);
        h.update_gesture(ms(170), [0.2, 1., 1.], [1.; 4]);
        assert_eq!(h.gesture, Gesture::Neutral); // no click while assembling the chord
        h.update_gesture(ms(180), [0.2, 0.2, 1.], [1.; 4]);
        h.update_gesture(ms(259), [0.2, 0.2, 1.], [1.; 4]);
        assert_eq!(h.gesture, Gesture::Neutral);
        h.update_gesture(ms(260), [0.2, 0.2, 1.], [1.; 4]);
        assert_eq!(h.gesture, Gesture::SpaceDrag);
        h.update_gesture(ms(280), [0.35, 0.35, 1.], [1.; 4]);
        assert_eq!(h.gesture, Gesture::SpaceDrag);
        h.update_gesture(ms(300), [0.2, 1., 1.], [1.; 4]);
        h.update_gesture(ms(359), [0.2, 1., 1.], [1.; 4]);
        assert_eq!(h.gesture, Gesture::SpaceDrag);
        h.update_gesture(ms(360), [0.2, 1., 1.], [1.; 4]);
        assert_eq!(h.gesture, Gesture::Neutral);
        h.update_gesture(ms(500), [0.2, 1., 1.], [1.; 4]);
        assert_eq!(h.gesture, Gesture::Neutral); // residual pinch cannot become a click
    }
    #[test]
    fn chord_is_offhand_only_and_role_change_requires_neutral() {
        let mut h = HandInput::default();
        for t in [0, 100] {
            h.update_gesture(ms(t), [1.; 3], [1.; 4]);
        }
        for t in [110, 210] {
            h.update_gesture(ms(t), [0.2, 0.2, 1.], [1.; 4]);
        }
        assert_eq!(h.gesture, Gesture::Neutral);
        h.set_space_drag_enabled(true);
        for t in [220, 320] {
            h.update_gesture(ms(t), [1.; 3], [1.; 4]);
        }
        for t in [330, 410] {
            h.update_gesture(ms(t), [0.2, 0.2, 1.], [1.; 4]);
        }
        assert_eq!(h.gesture, Gesture::SpaceDrag);
        h.set_space_drag_enabled(false);
        assert_eq!(h.gesture, Gesture::Neutral);
        assert!(!h.armed);
    }
    #[test]
    fn offhand_single_pinch_still_clicks_and_fist_remains_window_grip() {
        let mut h = HandInput::default();
        h.set_space_drag_enabled(true);
        for t in [0, 100] {
            h.update_gesture(ms(t), [1.; 3], [1.; 4]);
        }
        for t in [110, 230] {
            h.update_gesture(ms(t), [0.2, 1., 1.], [1.; 4]);
        }
        assert_eq!(h.gesture, Gesture::Click);
        h.reset();
        assert!(h.space_drag_enabled);
        for t in [240, 340] {
            h.update_gesture(ms(t), [1.; 3], [1.; 4]);
        }
        for t in [350, 430] {
            h.update_gesture(ms(t), [0.2, 0.2, 1.], [0.4; 4]);
        }
        assert_eq!(h.gesture, Gesture::Grab);
    }
    #[test]
    fn space_drag_uses_wrist_through_aim_loss_and_rearms_after_tracking_loss() {
        let mut h = HandInput::default();
        h.set_space_drag_enabled(true);
        let mut j = joints();
        for t in [0, 100] {
            h.update(
                ms(t),
                Some(&j),
                Some(Affine3A::IDENTITY),
                Affine3A::IDENTITY,
                true,
            );
        }
        j[10].pose.position = j[5].pose.position;
        j[15].pose.position = j[5].pose.position;
        for t in [110, 190] {
            h.update(
                ms(t),
                Some(&j),
                Some(Affine3A::IDENTITY),
                Affine3A::IDENTITY,
                true,
            );
        }
        assert_eq!(h.gesture, Gesture::SpaceDrag);
        j[0].pose.position.y = -0.7;
        let sample = h
            .update(ms(210), Some(&j), None, Affine3A::IDENTITY, true)
            .unwrap();
        assert_eq!(sample.gesture, Gesture::SpaceDrag);
        assert!(sample.ray.is_some());
        assert!(
            h.update(ms(220), None, None, Affine3A::IDENTITY, true)
                .is_none()
        );
        assert_eq!(h.gesture, Gesture::Neutral);
        assert!(h.space_drag_enabled);
        assert!(!h.armed);
    }
}
