// SPDX-License-Identifier: GPL-3.0-only
//! Shared fingertip geometry for keyboard selection, drawing and pinch contacts.
use glam::Vec3A;
use openxr as xr;

pub const MIN_RADIUS: f32 = 0.014;
const MAX_RADIUS: f32 = 0.020;
const PADDING: f32 = 0.007;
pub const RELEASE_SCALE: f32 = 1.5;

#[derive(Clone, Copy)]
pub struct Contacts {
    pub tips: [Vec3A; 3],
    pub radii: [f32; 3],
}
impl Contacts {
    pub fn from_joints(joints: &xr::HandJointLocations) -> Option<Self> {
        let indices = [5, 10, 15]; // OpenXR thumb/index/middle TIP, never aim poses.
        let flags = xr::SpaceLocationFlags::POSITION_VALID | xr::SpaceLocationFlags::POSITION_TRACKED;
        if indices.iter().any(|&i| !joints[i].location_flags.contains(flags)) {
            return None;
        }
        let tips = indices.map(|i| {
            let p = joints[i].pose.position;
            Vec3A::new(p.x, p.y, p.z)
        });
        if tips.iter().any(|p| !p.is_finite()) { return None; }
        let radii = indices.map(|i| {
            let radius = joints[i].radius;
            if radius.is_finite() { (radius + PADDING).clamp(MIN_RADIUS, MAX_RADIUS) }
            else { MIN_RADIUS }
        });
        Some(Self { tips, radii })
    }
    /// Retain the gesture detector's press/release hysteresis: 0.28 / 0.42.
    /// A press is exactly when the displayed spheres meet; release is 50% wider.
    pub fn pinch_ratios(self) -> [f32; 2] {
        [1, 2].map(|finger| self.tips[0].distance(self.tips[finger])
            / (self.radii[0] + self.radii[finger]) * 0.28)
    }
    pub fn neutral(self) -> bool {
        self.pinch_ratios().iter().all(|&r| r > 0.28 * RELEASE_SCALE)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn joints() -> xr::HandJointLocations {
        let joint = xr::HandJointLocation {
            location_flags: xr::SpaceLocationFlags::POSITION_VALID | xr::SpaceLocationFlags::POSITION_TRACKED,
            pose: xr::Posef::IDENTITY, radius: 0.005,
        };
        let mut joints = [joint; 26];
        for (i, x) in [(5, 0.0), (10, 0.027), (15, 0.06)] {
            joints[i].pose.position.x = x;
        }
        joints
    }
    #[test]
    fn contact_area_matches_visible_spheres_and_tracks_exact_tip_positions() {
        let mut j = joints();
        let a = Contacts::from_joints(&j).unwrap();
        assert_eq!(a.radii, [MIN_RADIUS; 3]);
        assert!(a.pinch_ratios()[0] < 0.28); // 27 mm is within two 14 mm spheres.
        assert!(a.pinch_ratios()[1] > 0.42);
        j[5].pose.position.y = 0.12;
        j[10].pose.position.z = -0.42;
        let b = Contacts::from_joints(&j).unwrap();
        assert_eq!(b.tips[0].y, 0.12);
        assert_eq!(b.tips[1].z, -0.42); // No filtering, offsets or previous-frame poses.
    }
    #[test]
    fn contact_radii_are_bounded_and_tracking_loss_cannot_reuse_positions() {
        let mut j = joints();
        j[5].radius = 0.2;
        j[10].radius = f32::NAN;
        assert_eq!(Contacts::from_joints(&j).unwrap().radii, [MAX_RADIUS, MIN_RADIUS, MIN_RADIUS]);
        j[10].location_flags = xr::SpaceLocationFlags::POSITION_VALID;
        assert!(Contacts::from_joints(&j).is_none());
        j[10].location_flags |= xr::SpaceLocationFlags::POSITION_TRACKED;
        j[15].pose.position.x = f32::NAN;
        assert!(Contacts::from_joints(&j).is_none());
    }
}
