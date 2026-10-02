// SPDX-License-Identifier: GPL-3.0-only
//! Surface contact hysteresis shared by the watch and virtual keyboard.
use std::time::{Duration, Instant};

#[derive(Default)]
pub struct TouchPress {
    armed: bool,
    pressed: bool,
    near_since: Option<Instant>,
}

impl TouchPress {
    pub fn update(&mut self, now: Instant, clearance: f32) -> bool {
        if !clearance.is_finite() || !(-0.020..=0.035).contains(&clearance) {
            *self = Self::default();
            return false;
        }
        if clearance >= 0.014 {
            self.armed = true;
            self.pressed = false;
            self.near_since = None;
        } else if self.armed && clearance <= 0.004 {
            let at = *self.near_since.get_or_insert(now);
            if now.saturating_duration_since(at) >= Duration::from_millis(25) {
                self.pressed = true;
                self.armed = false;
            }
        } else if !self.pressed {
            self.near_since = None;
        }
        self.pressed
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn approach_touch_hold_release_and_repeated_tap() {
        let t = Instant::now();
        let mut touch = TouchPress::default();
        assert!(!touch.update(t, 0.003)); // appearing inside a surface cannot tap
        assert!(!touch.update(t, 0.025));
        assert!(!touch.update(t + Duration::from_millis(10), 0.003));
        assert!(touch.update(t + Duration::from_millis(35), 0.003));
        assert!(touch.update(t + Duration::from_millis(40), 0.009)); // jitter
        assert!(touch.update(t + Duration::from_millis(50), -0.010)); // penetration
        assert!(!touch.update(t + Duration::from_millis(60), 0.020));
        assert!(!touch.update(t + Duration::from_millis(70), 0.003));
        assert!(touch.update(t + Duration::from_millis(95), 0.003));
        assert!(!touch.update(t + Duration::from_millis(100), f32::NAN));
        assert!(!touch.update(t + Duration::from_millis(130), 0.003));
    }
    #[test]
    fn crossing_back_or_leaving_target_releases_and_requires_new_approach() {
        let t = Instant::now();
        let mut touch = TouchPress::default();
        touch.update(t, 0.020);
        touch.update(t, 0.003);
        assert!(touch.update(t + Duration::from_millis(25), 0.003));
        assert!(!touch.update(t + Duration::from_millis(30), -0.03));
        assert!(!touch.update(t + Duration::from_millis(60), -0.005));
        assert!(!touch.update(t + Duration::from_millis(90), 0.003));
    }
}
