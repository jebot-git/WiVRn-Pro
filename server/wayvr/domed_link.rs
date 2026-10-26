// SPDX-License-Identifier: GPL-3.0-or-later
//! Bounded, nonblocking render-thread bridge to DomeD. D-Bus stays on one worker.
use dbus::blocking::Connection;
use serde::Serialize;
use serde_json::Value;
use std::{
    collections::VecDeque,
    sync::{
        Arc, Mutex,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};
static ACTIVE: AtomicBool = AtomicBool::new(false);
pub fn active() -> bool {
    ACTIVE.load(Ordering::Acquire)
}
static NOTIFICATIONS: AtomicBool = AtomicBool::new(false);
pub fn notifications() -> bool {
    NOTIFICATIONS.load(Ordering::Acquire)
}
#[derive(Clone, Serialize)]
pub struct Ray {
    pub tracked: bool,
    pub origin: [f32; 3],
    pub direction: [f32; 3],
    pub buttons: u8,
    pub scroll: [f64; 2],
    pub max_distance: f32,
}
#[derive(Clone, Serialize)]
pub struct Frame {
    pub version: u32,
    pub sequence: u64,
    pub time_ns: u64,
    pub rays: [Ray; 2],
}
pub fn now_ns() -> u64 {
    let mut t: libc::timespec = unsafe { std::mem::zeroed() };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut t) };
    t.tv_sec as u64 * 1_000_000_000 + t.tv_nsec as u64
}
#[derive(Clone)]
struct Notice {
    id: String,
    title: String,
    body: String,
    timeout: i32,
}
#[derive(Default)]
struct State {
    ready: Option<Instant>,
    frames: VecDeque<Frame>,
    commands: VecDeque<String>,
    notices: VecDeque<Notice>,
    hits: [Option<f32>; 2],
    feedback: Option<Instant>,
    notifications: bool,
}
pub struct Link {
    shared: Arc<Mutex<State>>,
    stop: Arc<AtomicBool>,
    thread: Option<std::thread::JoinHandle<()>>,
    sequence: u64,
    notice: u64,
    instance: u64,
}
impl Default for Link {
    fn default() -> Self {
        Self::new()
    }
}
impl Link {
    pub fn new() -> Self {
        let shared = Arc::new(Mutex::new(State::default()));
        let stop = Arc::new(AtomicBool::new(false));
        let state = shared.clone();
        let stopped = stop.clone();
        let thread = std::thread::Builder::new()
            .name("domed-wayvr".into())
            .spawn(move || run(state, stopped))
            .ok();
        Self {
            shared,
            stop,
            thread,
            sequence: 0,
            notice: 0,
            instance: now_ns(),
        }
    }
    pub fn active(&self) -> bool {
        self.shared
            .lock()
            .unwrap()
            .ready
            .is_some_and(|t| t.elapsed() < Duration::from_millis(1200))
    }
    pub fn command(&self, action: &str) -> bool {
        let mut s = self.shared.lock().unwrap();
        if s.ready.is_none() || s.commands.len() >= 16 {
            return false;
        }
        s.commands.push_back(action.into());
        true
    }
    pub fn notify(&mut self, title: &str, body: &str, timeout: f32) -> bool {
        if title.len() + body.len() > 60000 {
            return false;
        }
        let mut s = self.shared.lock().unwrap();
        if s.ready.is_none() || s.notices.len() >= 32 {
            return false;
        }
        self.notice += 1;
        s.notices.push_back(Notice {
            id: format!("wayvr-{}-{}", self.instance, self.notice),
            title: title.into(),
            body: body.into(),
            timeout: (timeout.clamp(0.1, 120.) * 1000.) as i32,
        });
        true
    }
    pub fn pointers(&mut self, rays: [Ray; 2]) {
        if !self.active() {
            return;
        }
        self.sequence += 1;
        let frame = Frame {
            version: 1,
            sequence: self.sequence,
            time_ns: now_ns(),
            rays,
        };
        let mut s = self.shared.lock().unwrap();
        if let Some(previous) = s.frames.back_mut() {
            // Coalesce stationary button states, never a press or release edge.
            if previous.rays.each_ref().map(|r| (r.buttons, r.tracked))
                == frame.rays.each_ref().map(|r| (r.buttons, r.tracked))
            {
                let mut frame = frame;
                for i in 0..2 {
                    for a in 0..2 {
                        frame.rays[i].scroll[a] =
                            (frame.rays[i].scroll[a] + previous.rays[i].scroll[a]).clamp(-20., 20.);
                    }
                }
                *previous = frame;
                return;
            }
        }
        if s.frames.len() >= 32 {
            s.frames.clear();
            let mut reset = frame;
            for r in &mut reset.rays {
                r.tracked = false;
                r.buttons = 0;
            }
            s.frames.push_back(reset);
        } else {
            s.frames.push_back(frame);
        }
    }
    pub fn hit(&self, hand: usize) -> Option<f32> {
        let s = self.shared.lock().unwrap();
        s.feedback
            .filter(|t| t.elapsed() < Duration::from_millis(150))
            .and(s.hits[hand])
    }
}
impl Drop for Link {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Release);
        if let Some(t) = self.thread.take() {
            let _ = t.join();
        }
    }
}
fn run(shared: Arc<Mutex<State>>, stop: Arc<AtomicBool>) {
    struct Clear;
    impl Drop for Clear {
        fn drop(&mut self) {
            ACTIVE.store(false, Ordering::Release);
            NOTIFICATIONS.store(false, Ordering::Release);
        }
    }
    let _clear = Clear;
    let mut connection = None;
    let mut poll = Instant::now();
    let mut notice_retry = Instant::now();
    while !stop.load(Ordering::Acquire) {
        if connection.is_none() {
            connection = Connection::new_session().ok();
            if connection.is_none() {
                std::thread::sleep(Duration::from_millis(200));
                continue;
            }
        }
        if !connection.as_ref().unwrap().channel().is_connected() {
            connection = None;
            let mut s = shared.lock().unwrap();
            s.ready = None;
            s.feedback = None;
            ACTIVE.store(false, Ordering::Release);
            NOTIFICATIONS.store(false, Ordering::Release);
            continue;
        }
        let c = connection.as_ref().unwrap();
        let proxy = c.with_proxy(
            "org.domed.Compositor",
            "/org/domed/Compositor",
            Duration::from_millis(100),
        );
        if Instant::now() >= poll {
            poll = Instant::now() + Duration::from_millis(250);
            let state = proxy
                .method_call::<(String,), _, _, _>(
                    "org.domed.Compositor",
                    "GetIntegrationState",
                    (),
                )
                .ok()
                .and_then(|r| serde_json::from_str::<Value>(&r.0).ok())
                .filter(|v| v["version"] == 1 && v["ready"] == true);
            let mut s = shared.lock().unwrap();
            if let Some(state) = state {
                s.ready = Some(Instant::now());
                s.notifications = state["notifications"] == "domed";
                NOTIFICATIONS.store(s.notifications, Ordering::Release);
                ACTIVE.store(true, Ordering::Release);
            } else {
                s.ready = None;
                s.notifications = false;
                NOTIFICATIONS.store(false, Ordering::Release);
                s.hits = [None; 2];
                s.frames.clear();
                s.commands.clear();
                ACTIVE.store(false, Ordering::Release);
            }
        }
        let ready = shared.lock().unwrap().ready.is_some();
        if ready {
            let command = shared.lock().unwrap().commands.pop_front();
            if let Some(action) = command {
                let _: Result<(bool,), _> =
                    proxy.method_call("org.domed.Compositor", "Activate", (action,));
            }
            // At most one input frame per worker turn; the compositor consumes every edge.
            let frame = shared.lock().unwrap().frames.pop_front();
            if let Some(frame) = frame {
                if now_ns().saturating_sub(frame.time_ns) <= 150_000_000 {
                    if let Ok((feedback,)) = proxy.method_call::<(String,), _, _, _>(
                        "org.domed.Compositor",
                        "SubmitPointers",
                        (serde_json::to_string(&frame).unwrap_or_default(),),
                    ) {
                        if let Ok(v) = serde_json::from_str::<Value>(&feedback) {
                            let mut s = shared.lock().unwrap();
                            s.hits = std::array::from_fn(|i| {
                                v["hits"][i]
                                    .as_f64()
                                    .filter(|v| v.is_finite() && *v >= 0. && *v <= 100.)
                                    .map(|v| v as f32)
                            });
                            s.feedback = Some(Instant::now());
                        }
                    }
                }
            }
            let notice = if Instant::now() >= notice_retry {
                shared.lock().unwrap().notices.pop_front()
            } else {
                None
            };
            if let Some(n) = notice {
                // Address DomeD directly. A restart must not auto-activate a host
                // notification daemon and create a second desktop popup.
                let result: Result<(bool,), _> = proxy.method_call(
                    "org.domed.Compositor",
                    "ForwardNotification",
                    (&n.id, &n.title, &n.body, n.timeout),
                );
                if !matches!(result, Ok((true,))) {
                    notice_retry = Instant::now() + Duration::from_secs(1);
                    let mut s = shared.lock().unwrap();
                    if s.notices.len() < 32 {
                        s.notices.push_front(n);
                    }
                }
            }
        }
        std::thread::sleep(Duration::from_millis(8));
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn link() -> Link {
        Link {
            shared: Arc::new(Mutex::new(State {
                ready: Some(Instant::now()),
                ..Default::default()
            })),
            stop: Arc::new(AtomicBool::new(false)),
            thread: None,
            sequence: 0,
            notice: 0,
            instance: 1,
        }
    }
    fn rays(buttons: u8) -> [Ray; 2] {
        std::array::from_fn(|_| Ray {
            tracked: true,
            origin: [0.; 3],
            direction: [0., 0., -1.],
            buttons,
            scroll: [0.; 2],
            max_distance: 100.,
        })
    }
    #[test]
    fn queued_frames_preserve_button_edges_and_overflow_releases() {
        let mut link = link();
        link.pointers(rays(0));
        link.pointers(rays(0));
        link.pointers(rays(1));
        link.pointers(rays(1));
        link.pointers(rays(0));
        assert_eq!(
            link.shared
                .lock()
                .unwrap()
                .frames
                .iter()
                .map(|f| f.rays[0].buttons)
                .collect::<Vec<_>>(),
            vec![0, 1, 0]
        );
        for i in 0..60 {
            link.pointers(rays((i % 2) as u8));
        }
        let s = link.shared.lock().unwrap();
        assert!(s.frames.len() <= 32);
        assert!(!s.frames[0].rays[0].tracked);
        assert_eq!(s.frames[0].rays[0].buttons, 0);
    }
    #[test]
    fn scroll_accumulates_but_feedback_expires_and_queues_are_bounded() {
        let mut link = link();
        let mut ray = rays(0);
        ray[0].scroll = [1., 2.];
        link.pointers(ray.clone());
        link.pointers(ray);
        assert_eq!(
            link.shared.lock().unwrap().frames[0].rays[0].scroll,
            [2., 4.]
        );
        {
            let mut s = link.shared.lock().unwrap();
            s.hits = [Some(2.), None];
            s.feedback = Some(Instant::now() - Duration::from_millis(151));
        }
        assert_eq!(link.hit(0), None);
        for _ in 0..16 {
            assert!(link.command("launcher"));
        }
        assert!(!link.command("launcher"));
        for _ in 0..32 {
            assert!(link.notify("one", "body", 1.));
        }
        assert!(!link.notify("one", "body", 1.));
        link.shared.lock().unwrap().ready = None;
        assert!(!link.active());
        assert!(!link.command("launcher"));
    }
}
