// SPDX-License-Identifier: GPL-3.0-only
//! Framing gestures and a bounded worker link. No render-thread process/file I/O.
use glam::{Affine3A, Vec3A};
use serde::{Deserialize, Serialize};
use std::{
    path::PathBuf,
    sync::{Arc, Mutex, mpsc},
    time::{Duration, Instant},
};
#[derive(Clone, Copy, Default, Debug)]
pub struct Hand {
    pub position: Vec3A,
    pub hold: bool,
    pub trigger: bool,
    pub valid: bool,
    pub armable: bool,
}
#[derive(Clone, Copy, Debug, Serialize)]
pub struct Rect {
    pub bounds: [f32; 4],
    pub depth: f32,
}
#[derive(Default)]
pub struct Model {
    pub active: bool,
    latched: bool,
    pub recording: bool,
    pub repeat: bool,
    pub mode: usize,
    pub rect: Option<Rect>,
    armed: bool,
    before: [bool; 2],
    right_down: Option<f64>,
    left_down: Option<f64>,
    frozen: Option<[Vec3A; 2]>,
    drag: Option<(usize, Vec3A)>,
    timer: Option<f64>,
    shot: Option<f64>,
    last_time: Option<f64>,
    pub caption: String,
    pub hide_guide: bool,
    zoom: f32,
}
#[derive(Default, Debug)]
pub struct Update {
    pub event: Option<&'static str>,
    pub owned: bool,
}
impl Model {
    pub fn reserving(&self) -> bool {
        self.active || self.latched
    }
    pub fn adjust_zoom(&mut self, delta: f32) {
        if self.active && delta.is_finite() {
            self.zoom = (self.zoom * delta.exp()).clamp(0.3, 3.);
        }
    }
    pub fn cancel(&mut self) {
        self.active = false;
        self.latched = false;
        self.recording = false;
        self.timer = None;
        self.shot = None;
        self.frozen = None;
        self.drag = None;
        self.rect = None;
        self.armed = false;
        self.right_down = None;
        self.left_down = None;
        self.before = [false; 2];
    }
    pub fn update(&mut self, now: f64, head: Affine3A, hands: [Hand; 2], blocked: bool) -> Update {
        let stale = self.last_time.is_some_and(|t| now <= t || now - t > 0.15);
        self.last_time = Some(now);
        if blocked
            || stale
            || !head.is_finite()
            || hands.iter().any(|h| !h.valid || !h.position.is_finite())
        {
            self.cancel();
            return Update::default();
        }
        let held = hands.map(|h| h.hold);
        let triggers = hands.map(|h| h.trigger);
        if !held[0] && !held[1] {
            self.armed = true;
        }
        if !self.active
            && self.armed
            && held.iter().all(|v| *v)
            && hands.iter().all(|h| h.armable)
            && hands[0].position.distance(hands[1].position) < 0.35
        {
            self.active = true;
            self.latched = true;
            self.zoom = 1.;
            self.armed = false;
            self.before = triggers;
            self.caption =
                "Spread hands to frame · left: mode · right: capture · both: timer".into();
        }
        if !self.active {
            if self.latched {
                if !held.iter().any(|v| *v) && !triggers.iter().any(|v| *v) {
                    self.latched = false;
                    self.armed = true;
                }
                return Update {
                    owned: true,
                    event: None,
                };
            }
            return Update::default();
        }
        if self.timer.is_none() && self.shot.is_none() && !held.iter().all(|v| *v) {
            let was = self.recording;
            self.cancel();
            return Update {
                event: was.then_some("stop"),
                owned: true,
            };
        }
        if self.timer.is_some() && (held[0] != held[1]) {
            let idx = if held[0] { 0 } else { 1 };
            if let Some((old, position)) = self.drag {
                if old == idx {
                    let delta = hands[idx].position - position;
                    if delta.length() > 0.15 {
                        self.cancel();
                        return Update::default();
                    }
                    self.frozen = self.frozen.map(|points| points.map(|point| point + delta));
                }
            }
            self.drag = Some((idx, hands[idx].position));
        } else {
            self.drag = None;
        }
        let points = self.frozen.unwrap_or(hands.map(|h| h.position));
        let p = points.map(|p| head.inverse().transform_point3a(p));
        if p.iter().any(|p| p.z >= -0.12 || p.z < -2.5) || points[0].distance(points[1]) > 1.8 {
            self.cancel();
            return Update::default();
        }
        let xy = p.map(|p| [p.x / -p.z, p.y / -p.z]);
        let mut bounds = [
            xy[0][0].min(xy[1][0]),
            xy[0][1].min(xy[1][1]),
            xy[0][0].max(xy[1][0]),
            xy[0][1].max(xy[1][1]),
        ];
        let center = [(bounds[0] + bounds[2]) * 0.5, (bounds[1] + bounds[3]) * 0.5];
        for i in 0..4 {
            bounds[i] = center[i % 2] + (bounds[i] - center[i % 2]) * self.zoom;
        }
        self.rect = Some(Rect {
            bounds,
            depth: ((-p[0].z - p[1].z) * 0.5).clamp(0.15, 2.),
        });
        let valid = bounds[2] - bounds[0] > 0.04 && bounds[3] - bounds[1] > 0.04;
        let mut event = None;
        if triggers[0]
            && triggers[1]
            && !self.before.iter().all(|v| *v)
            && !self.recording
            && self.shot.is_none()
        {
            self.timer = Some(now + 3.);
            self.frozen = Some(points);
            self.right_down = None;
            self.left_down = None;
        }
        if let Some(deadline) = self.timer {
            self.caption = format!("Self timer: {}", (deadline - now).ceil().max(0.));
            if now >= deadline && valid {
                self.timer = None;
                self.shot = Some(now + 0.12);
            }
        } else if let Some(deadline) = self.shot {
            if now >= deadline && valid {
                event = Some("capture");
                self.shot = None;
                if !self.repeat {
                    self.active = false;
                }
            }
        } else {
            if triggers[0] && !self.before[0] {
                self.left_down = Some(now);
            }
            if !triggers[0] && self.before[0] {
                if let Some(t) = self.left_down.take() {
                    if now - t >= 1. {
                        self.repeat = !self.repeat;
                    } else {
                        self.mode = (self.mode + 1) % 3;
                    }
                }
            }
            if triggers[1] && !self.before[1] {
                self.right_down = Some(now);
            }
            if triggers[1]
                && self.mode == 0
                && !self.recording
                && self.right_down.is_some_and(|t| now - t >= 1.)
                && valid
            {
                self.recording = true;
                event = Some("record");
            }
            if !triggers[1] && self.before[1] && self.right_down.take().is_some() {
                if self.recording {
                    event = Some("stop");
                    self.recording = false;
                    if !self.repeat {
                        self.active = false;
                    }
                } else if valid {
                    self.shot = Some(now + 0.12);
                    self.frozen = Some(points);
                }
            }
            self.caption = format!(
                "{}{} · {}",
                ["Photo", "Text / translation", "2D code"][self.mode],
                if self.repeat { " · repeat" } else { "" },
                if self.recording {
                    "Recording VP9 WebM (release to stop)"
                } else {
                    "Right trigger / index pinch to capture"
                }
            );
        }
        self.hide_guide =
            self.shot.is_some() || self.recording || (triggers[1] && self.right_down.is_some());
        self.before = triggers;
        Update { event, owned: true }
    }
}
#[derive(Clone, Default, Deserialize)]
pub struct Status {
    #[serde(default)]
    pub source: String,
    #[serde(default)]
    pub status: String,
    #[serde(default)]
    pub text: String,
    #[serde(default)]
    pub preview: String,
    #[serde(default)]
    pub bookmark_preview: String,
    #[serde(default)]
    pub revision: u64,
    #[serde(default)]
    pub finish_id: u64,
    #[serde(default)]
    pub language: String,
    #[serde(default)]
    pub page: String,
    #[serde(default)]
    pub bookmarked: bool,
    #[serde(default)]
    pub bookmarks: usize,
    #[serde(default)]
    pub open_id: u64,
    #[serde(skip)]
    pub png: Option<Arc<Vec<u8>>>,
    #[serde(skip)]
    pub bookmark_png: Option<Arc<Vec<u8>>>,
}
pub struct Feed {
    pub enabled: bool,
    pub open_id: u64,
    pub model: Model,
    pub status: Arc<Mutex<Status>>,
    sender: Option<mpsc::SyncSender<serde_json::Value>>,
    started: Instant,
    last_send: Option<Instant>,
    finish: u64,
    tail: Option<Instant>,
    stop: Arc<std::sync::atomic::AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
}
impl Default for Feed {
    fn default() -> Self {
        Self {
            enabled: false,
            open_id: 0,
            model: Model::default(),
            status: Arc::new(Mutex::new(Status::default())),
            sender: None,
            started: Instant::now(),
            last_send: None,
            finish: 0,
            tail: None,
            stop: Arc::new(std::sync::atomic::AtomicBool::new(false)),
            thread: None,
        }
    }
}
impl Feed {
    pub fn start(&mut self) {
        if let Some(tx) = &self.sender {
            match tx.try_send(serde_json::json!({"op":"ping"})) {
                Err(mpsc::TrySendError::Disconnected(_)) => self.sender = None,
                _ => return,
            }
        }
        let (tx, rx) = mpsc::sync_channel::<serde_json::Value>(32);
        self.sender = Some(tx);
        let shared = self.status.clone();
        let stop = self.stop.clone();
        stop.store(false, std::sync::atomic::Ordering::Relaxed);
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
        self.thread = std::thread::Builder::new()
            .name("hands-frame-worker".into())
            .spawn(move || {
                if let Err(e) = worker(rx, shared.clone(), stop) {
                    shared.lock().unwrap().status = format!("Hands Frame: {e:#}");
                }
            })
            .ok();
    }
    pub fn capturing(&self) -> bool {
        self.enabled
            && (self.model.active
                || self
                    .tail
                    .is_some_and(|t| t.elapsed() < Duration::from_millis(500)))
    }
    pub fn command(&self, op: &str) {
        if let Some(tx) = &self.sender {
            let _ = tx.try_send(serde_json::json!({"op":op}));
        }
    }
    pub fn sender(&self) -> Option<mpsc::SyncSender<serde_json::Value>> {
        self.sender.clone()
    }
    pub fn update(&mut self, head: Affine3A, hands: [Hand; 2], blocked: bool) -> bool {
        let finished = self.status.lock().unwrap().finish_id;
        if finished != self.finish {
            self.finish = finished;
            self.model.cancel();
        }
        let output = self.model.update(
            self.started.elapsed().as_secs_f64(),
            head,
            hands,
            blocked || !self.enabled,
        );
        if output.event == Some("capture") {
            self.tail = Some(Instant::now());
        }
        if output.event.is_some()
            || self
                .last_send
                .is_none_or(|t| t.elapsed() > Duration::from_millis(50))
        {
            self.last_send = Some(Instant::now());
            if let Some(tx) = &self.sender {
                let mut ts: libc::timespec = unsafe { std::mem::zeroed() };
                unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
                let time = ts.tv_sec as f64 + ts.tv_nsec as f64 / 1e9;
                let active = self.enabled
                    && self.model.rect.is_some()
                    && (self.model.active
                        || output.event.is_some()
                        || self
                            .tail
                            .is_some_and(|t| t.elapsed() < Duration::from_millis(500)));
                let _=tx.try_send(serde_json::json!({"op":"frame","time":time,"enabled":self.enabled,"active":active,"crop":self.model.rect.map(|r|r.bounds),"mode":(["photo","text","scan"][self.model.mode]),"repeat":self.model.repeat,"event":output.event}));
            }
        }
        output.owned
    }
}
impl Drop for Feed {
    fn drop(&mut self) {
        self.stop.store(true, std::sync::atomic::Ordering::Relaxed);
        self.sender.take();
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}

fn worker(
    rx: mpsc::Receiver<serde_json::Value>,
    status: Arc<Mutex<Status>>,
    stop: Arc<std::sync::atomic::AtomicBool>,
) -> anyhow::Result<()> {
    use std::{
        io::{BufRead, BufReader, Write},
        os::unix::fs::{MetadataExt, OpenOptionsExt},
        process::{Command, Stdio},
    };
    let runtime = PathBuf::from(
        std::env::var_os("XDG_RUNTIME_DIR")
            .ok_or_else(|| anyhow::anyhow!("No runtime directory"))?,
    )
    .join("wivrn-pro");
    std::fs::create_dir_all(&runtime)?;
    let info = std::fs::symlink_metadata(&runtime)?;
    anyhow::ensure!(
        info.is_dir() && info.uid() == unsafe { libc::getuid() } && info.mode() & 0o077 == 0,
        "Unsafe runtime directory"
    );
    let installed = std::env::current_exe()?
        .parent()
        .unwrap()
        .join("hands-frame-worker.py");
    let source = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../hands-frame-worker.py");
    let script = if installed.is_file() {
        installed
    } else {
        source
    };
    let data = std::env::var_os("XDG_DATA_HOME")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            PathBuf::from(std::env::var_os("HOME").unwrap_or_default()).join(".local/share")
        });
    let local = data.join("spatial-subtitles/venv/bin/python");
    let python = std::env::var_os("WIVRN_HANDS_FRAME_PYTHON")
        .map(PathBuf::from)
        .unwrap_or_else(|| {
            if local.is_file() {
                local
            } else {
                "python3".into()
            }
        });
    let mut child = Command::new(python)
        .arg("-u")
        .arg(script)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()?;
    let mut stdin = child.stdin.take().unwrap();
    use std::os::fd::AsRawFd;
    unsafe {
        let flags = libc::fcntl(stdin.as_raw_fd(), libc::F_GETFL);
        libc::fcntl(stdin.as_raw_fd(), libc::F_SETFL, flags | libc::O_NONBLOCK);
    }

    let stdout = child.stdout.take().unwrap();
    let shared = status.clone();
    let root = runtime.join("hands-frame");
    let reader = std::thread::spawn(move || {
        let mut stream = BufReader::new(stdout);
        let mut bytes = Vec::new();
        let mut previous = String::new();
        let mut png = None;
        let mut bookmark_previous = String::new();
        let mut bookmark_png = None;
        loop {
            bytes.clear();
            use std::io::Read;
            if stream
                .by_ref()
                .take(65537)
                .read_until(b'\n', &mut bytes)
                .ok()
                .filter(|n| *n > 0 && *n <= 65536)
                .is_none()
            {
                break;
            }
            if let Ok(mut sample) = serde_json::from_slice::<Status>(&bytes) {
                if sample.preview != previous {
                    previous = sample.preview.clone();
                    png = None;
                    if !previous.is_empty()
                        && previous.len() < 80
                        && previous.bytes().all(|c| {
                            c.is_ascii_hexdigit()
                                || c == b'.'
                                || c == b'p'
                                || c == b'n'
                                || c == b'g'
                        })
                        && previous.ends_with(".png")
                    {
                        let path = root.join(&previous);
                        if let Ok(meta) = std::fs::symlink_metadata(&path) {
                            if meta.is_file()
                                && meta.uid() == unsafe { libc::getuid() }
                                && meta.mode() & 0o077 == 0
                                && meta.len() <= 16 * 1024 * 1024
                            {
                                png = std::fs::read(path).ok().map(Arc::new);
                            }
                        }
                    }
                }
                if sample.bookmark_preview != bookmark_previous {
                    bookmark_previous = sample.bookmark_preview.clone();
                    bookmark_png = None;
                    if bookmark_previous == sample.preview {
                        bookmark_png = png.clone();
                    } else if bookmark_previous.len() == 36
                        && bookmark_previous.ends_with(".png")
                        && bookmark_previous[..32]
                            .bytes()
                            .all(|c| c.is_ascii_hexdigit())
                    {
                        let path = root.join(&bookmark_previous);
                        if let Ok(meta) = std::fs::symlink_metadata(&path) {
                            if meta.is_file()
                                && meta.uid() == unsafe { libc::getuid() }
                                && meta.mode() & 0o077 == 0
                                && meta.len() <= 16 * 1024 * 1024
                            {
                                bookmark_png = std::fs::read(path).ok().map(Arc::new);
                            }
                        }
                    }
                }
                sample.bookmark_png = bookmark_png.clone();
                sample.png = png.clone();
                *shared.lock().unwrap() = sample;
            }
        }
        shared.lock().unwrap().status =
            "Hands Frame worker stopped; check Python/Pillow and FFmpeg installation".into();
    });
    let lease = runtime.join("hands-frame.lease");
    while !stop.load(std::sync::atomic::Ordering::Relaxed) {
        let command = match rx.recv_timeout(Duration::from_millis(50)) {
            Ok(c) => c,
            Err(mpsc::RecvTimeoutError::Timeout) => continue,
            Err(_) => break,
        };
        let active = command
            .get("active")
            .and_then(|v| v.as_bool())
            .unwrap_or(false);
        if matches!(
            command.get("op").and_then(|v| v.as_str()),
            Some("cancel" | "quit")
        ) {
            let _ = std::fs::remove_file(&lease);
        }
        if command.get("op") == Some(&serde_json::json!("frame")) {
            if active {
                let mut ts: libc::timespec = unsafe { std::mem::zeroed() };
                unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
                let now = ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64;
                let mut f = std::fs::OpenOptions::new()
                    .create(true)
                    .write(true)
                    .truncate(true)
                    .mode(0o600)
                    .custom_flags(libc::O_NOFOLLOW)
                    .open(&lease)?;
                f.write_all(&now.to_le_bytes())?;
            } else {
                let _ = std::fs::remove_file(&lease);
            }
        }
        if writeln!(stdin, "{command}").is_err() {
            break;
        }
        if child.try_wait()?.is_some() {
            break;
        }
    }
    let _ = std::fs::remove_file(lease);
    drop(stdin);
    let deadline = Instant::now() + Duration::from_secs(2);
    while child.try_wait()?.is_none() && Instant::now() < deadline {
        std::thread::sleep(Duration::from_millis(20));
    }
    let _ = child.kill();
    let _ = child.wait();
    let _ = reader.join();
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    fn hands(hold: bool, trigger: [bool; 2]) -> [Hand; 2] {
        [
            Hand {
                position: Vec3A::new(-0.05, 0.05, -0.5),
                hold,
                trigger: trigger[0],
                valid: true,
                armable: true,
            },
            Hand {
                position: Vec3A::new(0.05, -0.05, -0.5),
                hold,
                trigger: trigger[1],
                valid: true,
                armable: true,
            },
        ]
    }
    fn start(m: &mut Model, t: &mut f64) {
        *t += 0.02;
        m.update(*t, Affine3A::IDENTITY, hands(false, [false; 2]), false);
        *t += 0.02;
        m.update(*t, Affine3A::IDENTITY, hands(true, [false; 2]), false);
        assert!(m.active);
    }
    fn tick(m: &mut Model, t: &mut f64, hold: bool, triggers: [bool; 2]) -> Update {
        *t += 0.02;
        m.update(*t, Affine3A::IDENTITY, hands(hold, triggers), false)
    }
    #[test]
    fn held_gestures_need_neutral_and_cancellation_never_captures() {
        let mut m = Model::default();
        let mut t = 0.;
        assert!(
            !m.update(t, Affine3A::IDENTITY, hands(true, [true; 2]), false)
                .owned
        );
        start(&mut m, &mut t);
        t += 0.02;
        assert!(
            !m.update(t, Affine3A::IDENTITY, hands(true, [true; 2]), true)
                .owned
        );
        assert!(!tick(&mut m, &mut t, true, [false; 2]).owned);
        start(&mut m, &mut t);
        t += 0.3;
        assert!(
            !m.update(t, Affine3A::IDENTITY, hands(true, [false; 2]), false)
                .owned
        );
    }
    #[test]
    fn short_confirmation_hides_guide_before_photo() {
        let mut m = Model::default();
        let mut t = 0.;
        start(&mut m, &mut t);
        tick(&mut m, &mut t, true, [false, true]);
        tick(&mut m, &mut t, true, [false, false]);
        assert!(m.hide_guide);
        let mut shots = 0;
        for _ in 0..12 {
            shots +=
                usize::from(tick(&mut m, &mut t, true, [false, false]).event == Some("capture"));
        }
        assert_eq!(shots, 1);
        assert!(!m.active);
    }
    #[test]
    fn long_confirmation_records_once_and_release_stops() {
        let mut m = Model::default();
        let mut t = 0.;
        start(&mut m, &mut t);
        let mut starts = 0;
        for _ in 0..70 {
            starts +=
                usize::from(tick(&mut m, &mut t, true, [false, true]).event == Some("record"));
        }
        assert_eq!(starts, 1);
        assert!(m.recording);
        assert_eq!(
            tick(&mut m, &mut t, true, [false, false]).event,
            Some("stop")
        );
        assert!(!m.active);
    }
    #[test]
    fn two_triggers_start_timer_and_hold_release_does_not_cancel_it() {
        let mut m = Model::default();
        let mut t = 0.;
        start(&mut m, &mut t);
        tick(&mut m, &mut t, true, [true, true]);
        assert!(m.timer.is_some());
        let mut shots = 0;
        for _ in 0..170 {
            shots +=
                usize::from(tick(&mut m, &mut t, false, [false, false]).event == Some("capture"));
        }
        assert_eq!(shots, 1);
        assert_eq!(m.mode, 0);
    }
    #[test]
    fn mode_and_repeat_do_not_change_on_a_held_activation_trigger() {
        let mut m = Model::default();
        let mut t = 0.;
        start(&mut m, &mut t);
        tick(&mut m, &mut t, true, [true, false]);
        tick(&mut m, &mut t, true, [false, false]);
        assert_eq!(m.mode, 1);
        for _ in 0..60 {
            tick(&mut m, &mut t, true, [true, false]);
        }
        tick(&mut m, &mut t, true, [false, false]);
        assert!(m.repeat);
        assert_eq!(m.mode, 1);
    }
    #[test]
    fn completed_capture_owns_held_grips_until_neutral() {
        let mut m = Model::default();
        let mut t = 0.;
        start(&mut m, &mut t);
        tick(&mut m, &mut t, true, [false, true]);
        tick(&mut m, &mut t, true, [false, false]);
        for _ in 0..12 {
            tick(&mut m, &mut t, true, [false, false]);
        }
        assert!(!m.active && m.reserving());
        assert!(tick(&mut m, &mut t, true, [false, true]).owned);
        assert!(tick(&mut m, &mut t, false, [false, false]).owned);
        assert!(!tick(&mut m, &mut t, false, [false, false]).owned);
    }
}
