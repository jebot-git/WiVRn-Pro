// SPDX-License-Identifier: GPL-3.0-only
// Adapted from local KWin VrMousePriorityState (GPL-2.0-or-later): physical
// pointer motion/scroll and held buttons win; resume after 1500 ms idle.
use evdev::{Device, KeyCode};
use std::{
    collections::HashMap,
    path::PathBuf,
    time::{Duration, Instant},
};

pub struct MousePriority {
    devices: HashMap<PathBuf, Device>,
    last_scan: Option<Instant>,
    last_activity: Option<Instant>,
    held: Vec<u16>,
    captured: Vec<u16>,
    unavailable: bool,
}
impl Default for MousePriority {
    fn default() -> Self {
        Self {
            devices: HashMap::new(),
            last_scan: None,
            last_activity: None,
            held: vec![],
            captured: vec![],
            unavailable: false,
        }
    }
}
impl MousePriority {
    pub fn activity(&mut self) {
        self.last_activity = Some(Instant::now());
    }
    pub fn button(&mut self, code: u16, pressed: bool) {
        self.activity();
        self.captured.retain(|v| *v != code);
        if pressed {
            self.captured.push(code);
        }
    }
    pub fn clear_capture(&mut self) {
        self.captured.clear();
        self.activity();
    }
    pub fn captured_button_held(&self, code: u16) -> bool {
        self.captured.contains(&code)
    }
    pub fn button_held(&self, code: u16) -> bool {
        self.held.contains(&code) || self.captured.contains(&code)
    }
    pub fn active(&self) -> bool {
        self.unavailable
            || !self.held.is_empty()
            || !self.captured.is_empty()
            || self
                .last_activity
                .is_some_and(|t| t.elapsed() < Duration::from_millis(1500))
    }
    pub fn poll(&mut self) {
        let now = Instant::now();
        if self
            .last_scan
            .is_none_or(|t| now.duration_since(t) > Duration::from_secs(2))
        {
            let initial = self.last_scan.is_none();
            self.last_scan = Some(now);
            let mut denied = false;
            if let Ok(entries) = std::fs::read_dir("/dev/input") {
                for entry in entries.flatten() {
                    let path = entry.path();
                    if !entry.file_name().to_string_lossy().starts_with("event") {
                        continue;
                    }
                    if self.devices.contains_key(&path) {
                        continue;
                    }
                    let Ok(device) = Device::open(&path) else {
                        denied = true;
                        continue;
                    };
                    if device
                        .name()
                        .is_some_and(|s| s.starts_with("WayVR") || s.starts_with("WlxOverlay"))
                    {
                        continue;
                    }
                    if !device
                        .supported_keys()
                        .is_some_and(|s| s.contains(KeyCode::BTN_LEFT))
                    {
                        continue;
                    }
                    if device.set_nonblocking(true).is_ok() {
                        self.devices.insert(path, device);
                    }
                }
            } else {
                denied = true;
            }
            let unavailable = denied;
            if unavailable != self.unavailable {
                log::warn!("Physical mouse monitoring unavailable: {unavailable}");
            }
            self.unavailable = unavailable;
            if initial {
                log::info!(
                    "Physical mouse priority: {} passive pointer devices, available={}",
                    self.devices.len(),
                    !unavailable
                );
            }
        }
        let old = self.held.clone();
        self.held.clear();
        let mut activity = false;
        self.devices.retain(|path, device| {
            if !path.exists() {
                return false;
            }
            match device.fetch_events() {
                Ok(events) => {
                    for ev in events.take(512) {
                        let ty = ev.event_type().0;
                        if (ty == 2 && ev.value() != 0)
                            || ty == 3
                            || (ty == 1 && (0x110..0x120).contains(&ev.code()))
                        {
                            activity = true;
                        }
                    }
                }
                Err(e) if e.kind() == std::io::ErrorKind::WouldBlock => {}
                Err(_) => return false,
            }
            if let Ok(keys) = device.get_key_state() {
                for key in keys.iter() {
                    if (0x110..0x120).contains(&key.0) {
                        self.held.push(key.0);
                    }
                }
            }
            true
        });
        self.held.sort_unstable();
        self.held.dedup();
        if activity || old != self.held {
            self.last_activity = Some(now);
        }
    }
}
#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn held_mouse_wins_until_release_and_idle() {
        let mut m = MousePriority::default();
        assert!(!m.active());
        m.button(0x110, true);
        m.last_activity = Some(Instant::now() - Duration::from_secs(3));
        assert!(m.active());
        assert!(m.button_held(0x110));
        m.button(0x110, false);
        assert!(m.active());
        m.last_activity = Some(Instant::now() - Duration::from_secs(2));
        assert!(!m.active());
    }
}

#[cfg(test)]
mod arbitration_tests {
    use super::*;
    #[test]
    fn multiple_buttons_and_capture_loss_do_not_leave_priority_stuck() {
        let mut m = MousePriority::default();
        m.button(0x110, true);
        m.button(0x111, true);
        m.button(0x110, false);
        m.last_activity = Some(Instant::now() - Duration::from_secs(3));
        assert!(m.active());
        assert!(!m.button_held(0x110));
        assert!(m.button_held(0x111));
        m.clear_capture();
        assert!(m.active());
        m.last_activity = Some(Instant::now() - Duration::from_secs(3));
        assert!(!m.active());
        m.held.push(0x112);
        assert!(m.active());
        m.held.clear();
        m.unavailable = true;
        assert!(m.active());
    }
}
