// SPDX-License-Identifier: GPL-3.0-only
use glam::{Affine3A, Vec3, Vec3A};

#[derive(Clone, Copy)]
pub struct HandResize {
    initial: Affine3A,
    midpoint: Vec3A,
    span: f32,
}
impl HandResize {
    pub fn start(initial: Affine3A, left: Vec3A, right: Vec3A) -> Option<Self> {
        let span = left.distance(right);
        let scale = Vec3::new(initial.x_axis.length(), initial.y_axis.length(), initial.z_axis.length());
        (initial.is_finite() && left.is_finite() && right.is_finite() && span >= 0.08 && scale.min_element() > 0.0 && scale.max_element() / scale.min_element() <= 200.0)
            .then_some(Self { initial, midpoint: (left + right) * 0.5, span })
    }
    pub fn update(self, left: Vec3A, right: Vec3A) -> Option<Affine3A> {
        if !left.is_finite() || !right.is_finite() { return None; }
        let scale = Vec3::new(self.initial.x_axis.length(), self.initial.y_axis.length(), self.initial.z_axis.length());
        let ratio = (left.distance(right) / self.span).clamp(0.1 / scale.min_element(), 20.0 / scale.max_element());
        let result = Affine3A {
            matrix3: self.initial.matrix3.mul_scalar(ratio),
            translation: (left + right) * 0.5 + (self.initial.translation - self.midpoint) * ratio,
        };
        result.is_finite().then_some(result)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use glam::Quat;
    #[test]
    fn two_fists_expand_contract_and_translate_without_rotating() {
        let initial = Affine3A::from_scale_rotation_translation(Vec3::splat(2.0), Quat::from_rotation_y(0.7), Vec3::new(1.0, 2.0, -2.0));
        let state = HandResize::start(initial, Vec3A::new(-0.2, 0.0, 0.0), Vec3A::new(0.2, 0.0, 0.0)).unwrap();
        let unchanged = state.update(Vec3A::new(-0.2, 0.0, 0.0), Vec3A::new(0.2, 0.0, 0.0)).unwrap();
        assert!(unchanged.abs_diff_eq(initial, 1e-5));
        let big = state.update(Vec3A::new(0.6, 0.0, 0.0), Vec3A::new(1.4, 0.0, 0.0)).unwrap();
        assert!((big.x_axis.length() - 4.0).abs() < 1e-5);
        assert!(big.translation.abs_diff_eq(Vec3A::new(3.0, 4.0, -4.0), 1e-5));
        assert!(big.x_axis.normalize().abs_diff_eq(initial.x_axis.normalize(), 1e-5));
        let small = state.update(Vec3A::new(-0.1, 0.0, 0.0), Vec3A::new(0.1, 0.0, 0.0)).unwrap();
        assert!((small.x_axis.length() - 1.0).abs() < 1e-5);
    }
    #[test]
    fn singular_nonfinite_and_extreme_input_is_bounded() {
        assert!(HandResize::start(Affine3A::IDENTITY, Vec3A::ZERO, Vec3A::ZERO).is_none());
        let state = HandResize::start(Affine3A::IDENTITY, Vec3A::ZERO, Vec3A::X).unwrap();
        assert!(state.update(Vec3A::splat(f32::NAN), Vec3A::X).is_none());
        assert!((state.update(Vec3A::ZERO, Vec3A::ZERO).unwrap().x_axis.length() - 0.1).abs() < 1e-5);
        assert!((state.update(Vec3A::ZERO, Vec3A::X * 100.0).unwrap().x_axis.length() - 20.0).abs() < 1e-5);
    }
}
