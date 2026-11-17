// SPDX-License-Identifier: GPL-3.0-only
// Adapted from local KWin VrMousePriorityState (GPL-2.0-or-later): physical
// pointer motion/scroll and held buttons win; resume after 1500 ms idle.
use evdev::{Device, KeyCode};
use std::{
    collections::HashMap,
    path::{Path, PathBuf},
    sync::mpsc::{self, Receiver, TryRecvError, TrySendError},
    time::{Duration, Instant},
};

pub struct MousePriority {
    monitor: Option<Receiver<MouseSnapshot>>,
    started: bool,
    last_activity: Option<Instant>,
    held: Vec<u16>,
    captured: Vec<u16>,
    unavailable: bool,
}
impl Default for MousePriority {
    fn default() -> Self {
        Self {
            monitor: None,
            started: false,
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
        if !self.started {
            self.started = true;
            let (tx, rx) = mpsc::sync_channel(1);
            // Closing an evdev fd can wait for an RCU grace period. Reopening
            // non-pointer devices on the XR thread caused >300 ms frame gaps.
            match std::thread::Builder::new()
                .name("wayvr-mouse-priority".into())
                .spawn(move || {
                    let mut devices = MouseDevices::default();
                    loop {
                        devices.poll();
                        match tx.try_send(devices.snapshot()) {
                            Ok(()) | Err(TrySendError::Full(_)) => {}
                            Err(TrySendError::Disconnected(_)) => break,
                        }
                        std::thread::sleep(Duration::from_millis(10));
                    }
                }) {
                Ok(_) => {
                    self.monitor = Some(rx);
                    self.unavailable = true; // wait for the initial held-button snapshot
                }
                Err(error) => {
                    log::warn!("Could not start physical mouse monitoring: {error}");
                    self.unavailable = true;
                }
            }
        }
        let Some(rx) = &self.monitor else { return };
        // One bounded snapshot; never wait for discovery, ioctls or device close.
        match rx.try_recv() {
            Ok(snapshot) => {
                self.held = snapshot.held;
                self.last_activity = self.last_activity.max(snapshot.last_activity);
                self.unavailable = snapshot.unavailable;
            }
            Err(TryRecvError::Empty) => {}
            Err(TryRecvError::Disconnected) => {
                self.held.clear();
                self.unavailable = true;
            }
        }
    }
}

#[derive(Default)]
struct MouseDevices {
    devices: HashMap<PathBuf, Device>,
    ignored: HashMap<PathBuf, PathBuf>,
    last_scan: Option<Instant>,
    last_activity: Option<Instant>,
    held: Vec<u16>,
    unavailable: bool,
}
struct MouseSnapshot {
    held: Vec<u16>,
    last_activity: Option<Instant>,
    unavailable: bool,
}

/// uinput devices are software injection, including WiVRn's own mouse. A real
/// Bluetooth mouse lives below virtual/misc/uhid, so don't reject all virtual/.
pub fn is_virtual_input(path: &Path, name: Option<&str>) -> bool {
    let device = path.file_name().and_then(|event| {
        std::fs::canonicalize(Path::new("/sys/class/input").join(event).join("device")).ok()
    });
    virtual_input_identity(device.as_deref(), name)
}

fn virtual_input_identity(device: Option<&Path>, name: Option<&str>) -> bool {
    device.is_some_and(|p| p.starts_with("/sys/devices/virtual/input"))
        || name.is_some_and(|s| {
            ["WayVR", "WlxOverlay", "WiVRn", "ydotool"]
                .iter()
                .any(|prefix| s.starts_with(prefix))
        })
}

impl MouseDevices {
    fn snapshot(&self) -> MouseSnapshot {
        MouseSnapshot {
            held: self.held.clone(),
            last_activity: self.last_activity,
            unavailable: self.unavailable,
        }
    }
    fn poll(&mut self) {
        let now = Instant::now();
        if self
            .last_scan
            .is_none_or(|t| now.duration_since(t) > Duration::from_secs(2))
        {
            let initial = self.last_scan.is_none();
            self.last_scan = Some(now);
            let mut denied = false;
            if let Ok(entries) = std::fs::read_dir("/dev/input") {
                let mut ignored = HashMap::new();
                for entry in entries.flatten() {
                    let path = entry.path();
                    if !entry.file_name().to_string_lossy().starts_with("event") {
                        continue;
                    }
                    if self.devices.contains_key(&path) {
                        continue;
                    }
                    let identity = std::fs::canonicalize(
                        Path::new("/sys/class/input")
                            .join(entry.file_name())
                            .join("device"),
                    )
                    .ok();
                    if virtual_input_identity(identity.as_deref(), None) {
                        continue;
                    }
                    if let Some(id) = &identity {
                        if self.ignored.get(&path) == Some(id) {
                            ignored.insert(path, id.clone());
                            continue;
                        }
                    }
                    let Ok(device) = Device::open(&path) else {
                        denied = true;
                        continue;
                    };
                    if virtual_input_identity(identity.as_deref(), device.name())
                        || !device
                            .supported_keys()
                            .is_some_and(|s| s.contains(KeyCode::BTN_LEFT))
                    {
                        if let Some(id) = identity {
                            ignored.insert(path, id);
                        }
                        continue;
                    }
                    if device.set_nonblocking(true).is_ok() {
                        self.devices.insert(path, device);
                    }
                }
                self.ignored = ignored;
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
    fn synthetic_input_is_excluded_but_usb_and_bluetooth_mice_are_preserved() {
        assert!(virtual_input_identity(
            Some(Path::new("/sys/devices/virtual/input/input33")),
            Some("Other virtual mouse")
        ));
        for name in [
            "WiVRn Mouse",
            "WayVR Mouse",
            "WlxOverlay Mouse",
            "ydotoold virtual device",
        ] {
            assert!(virtual_input_identity(None, Some(name)));
        }
        for path in [
            "/sys/devices/pci0000:00/usb1/1-1/input/input9",
            "/sys/devices/virtual/misc/uhid/0005:047D:8019.0009/input/input36",
        ] {
            assert!(!virtual_input_identity(
                Some(Path::new(path)),
                Some("ExpertBT5.0 Mouse")
            ));
        }
    }
    #[test]
    fn stalled_monitor_does_not_block_frames_or_lose_captured_buttons() {
        let (tx, rx) = mpsc::sync_channel(1);
        let mut m = MousePriority {
            monitor: Some(rx),
            started: true,
            ..Default::default()
        };
        m.button(0x110, true);
        let activity = m.last_activity;
        m.poll(); // a worker with no snapshot must not wait
        assert!(m.active());
        tx.send(MouseSnapshot {
            held: vec![0x111],
            last_activity: Some(Instant::now() - Duration::from_secs(3)),
            unavailable: false,
        })
        .unwrap();
        m.poll();
        assert_eq!(m.last_activity, activity);
        assert!(m.button_held(0x110) && m.button_held(0x111));
        tx.send(MouseSnapshot {
            held: vec![],
            last_activity: None,
            unavailable: false,
        })
        .unwrap();
        m.poll();
        assert!(m.captured_button_held(0x110));
        assert!(!m.button_held(0x111));
        drop(tx);
        m.poll();
        assert!(m.unavailable && m.active());
    }
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
