// SPDX-License-Identifier: GPL-3.0-only
use glam::Vec3;
use std::time::Duration;
#[derive(Default)]
pub struct DoubleTap {
    down: Option<(Duration, Vec3)>,
    previous: Option<Duration>,
    moved: bool,
}
impl DoubleTap {
    pub fn update(&mut self, now: Duration, pressed: bool, wrist: Vec3) -> bool {
        if pressed {
            let (_, start) = *self.down.get_or_insert((now, wrist));
            self.moved |= start.distance(wrist) > 0.03;
            return false;
        }
        let Some((start, _)) = self.down.take() else {
            return false;
        };
        let tap = !self.moved && now.saturating_sub(start) <= Duration::from_millis(300);
        self.moved = false;
        if !tap {
            self.previous = None;
            return false;
        }
        if self
            .previous
            .take()
            .is_some_and(|t| start.saturating_sub(t) <= Duration::from_millis(350))
        {
            return true;
        }
        self.previous = Some(now);
        false
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn quick_double_tap_resets_but_drag_does_not() {
        let mut d = DoubleTap::default();
        let ms = Duration::from_millis;
        assert!(!d.update(ms(0), true, Vec3::ZERO));
        assert!(!d.update(ms(150), false, Vec3::ZERO));
        assert!(!d.update(ms(250), true, Vec3::ZERO));
        assert!(d.update(ms(400), false, Vec3::ZERO));
        assert!(!d.update(ms(500), true, Vec3::ZERO));
        assert!(!d.update(ms(550), true, Vec3::X));
        assert!(!d.update(ms(650), false, Vec3::X));
        assert!(!d.update(ms(700), true, Vec3::X));
        assert!(!d.update(ms(800), false, Vec3::X));
    }
}
