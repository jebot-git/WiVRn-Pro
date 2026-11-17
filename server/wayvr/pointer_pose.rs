// SPDX-License-Identifier: GPL-3.0-or-later
use glam::{Affine3A, Quat, Vec3};
use openxr as xr;

/// Missing position may contain the origin even when orientation remains valid.
/// Return None on loss/idle so both cached transforms remain at their last pose.
pub fn update(
    current: Affine3A,
    pose: xr::Posef,
    flags: xr::SpaceLocationFlags,
    lerp_factor: Option<f32>,
    require_tracking: bool,
) -> Option<(Affine3A, Affine3A)> {
    let mut required =
        xr::SpaceLocationFlags::POSITION_VALID | xr::SpaceLocationFlags::ORIENTATION_VALID;
    if require_tracking {
        required |=
            xr::SpaceLocationFlags::POSITION_TRACKED | xr::SpaceLocationFlags::ORIENTATION_TRACKED;
    }
    if !flags.contains(required) {
        return None;
    }
    let position = Vec3::new(pose.position.x, pose.position.y, pose.position.z);
    let rotation = Quat::from_xyzw(
        pose.orientation.x,
        pose.orientation.y,
        pose.orientation.z,
        pose.orientation.w,
    );
    if !position.is_finite() || !rotation.is_finite() || rotation.length_squared() < 0.1 {
        return None;
    }
    let rotation = rotation.normalize();
    let raw = Affine3A::from_rotation_translation(rotation, position);
    let filtered = if let Some(factor) = lerp_factor.filter(|_| current.is_finite()) {
        Affine3A::from_rotation_translation(
            Quat::from_affine3(&current)
                .lerp(rotation, factor)
                .normalize(),
            Vec3::from(current.translation).lerp(position, factor),
        )
    } else {
        raw
    };
    Some((raw, filtered))
}

#[cfg(test)]
mod tests {
    use super::*;
    fn tracked() -> xr::SpaceLocationFlags {
        xr::SpaceLocationFlags::POSITION_VALID
            | xr::SpaceLocationFlags::ORIENTATION_VALID
            | xr::SpaceLocationFlags::POSITION_TRACKED
            | xr::SpaceLocationFlags::ORIENTATION_TRACKED
    }

    #[test]
    fn lost_position_idle_and_invalid_pose_leave_last_transform_untouched() {
        let current = Affine3A::from_rotation_translation(
            Quat::from_rotation_y(0.7),
            Vec3::new(1.0, 2.0, 3.0),
        );
        let mut sample = xr::Posef::IDENTITY;
        for flags in [
            xr::SpaceLocationFlags::ORIENTATION_VALID | xr::SpaceLocationFlags::ORIENTATION_TRACKED,
            xr::SpaceLocationFlags::POSITION_VALID | xr::SpaceLocationFlags::ORIENTATION_VALID,
            xr::SpaceLocationFlags::EMPTY,
        ] {
            assert!(update(current, sample, flags, Some(0.3), true).is_none());
        }
        sample.position.x = f32::NAN;
        assert!(update(current, sample, tracked(), None, true).is_none());
        sample.position.x = 1.0;
        sample.orientation.w = 0.0;
        assert!(update(current, sample, tracked(), None, true).is_none());
    }

    #[test]
    fn tracking_reacquisition_updates_raw_pose_and_smooths_from_frozen_pose() {
        let frozen = Affine3A::from_translation(Vec3::new(1.0, 2.0, 3.0));
        let sample = xr::Posef {
            orientation: xr::Quaternionf::IDENTITY,
            position: xr::Vector3f {
                x: 3.0,
                y: 2.0,
                z: 3.0,
            },
        };
        let (raw, smooth) = update(frozen, sample, tracked(), Some(0.5), true).unwrap();
        assert_eq!(Vec3::from(raw.translation), Vec3::new(3.0, 2.0, 3.0));
        assert_eq!(Vec3::from(smooth.translation), Vec3::new(2.0, 2.0, 3.0));
    }

    #[test]
    fn explicit_gaze_pointer_can_use_a_valid_untracked_pose() {
        let flags =
            xr::SpaceLocationFlags::POSITION_VALID | xr::SpaceLocationFlags::ORIENTATION_VALID;
        assert!(update(Affine3A::IDENTITY, xr::Posef::IDENTITY, flags, None, false).is_some());
    }
}
