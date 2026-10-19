// SPDX-License-Identifier: GPL-3.0-only
//! Live logical output geometry used by absolute desktop pointer injection.
use glam::{DVec2, Vec2};
use std::sync::{
    Arc, Mutex,
    atomic::{AtomicBool, Ordering},
};
use std::thread::JoinHandle;
use std::time::Duration;

#[derive(Clone, Debug, PartialEq)]
pub struct Output {
    pub name: String,
    pub position: Vec2,
    pub size: Vec2,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Layout {
    pub outputs: Vec<Output>,
    pub origin: DVec2,
    pub extent: DVec2,
}
impl Layout {
    pub fn new(mut outputs: Vec<Output>) -> Option<Self> {
        outputs
            .retain(|o| o.position.is_finite() && o.size.is_finite() && o.size.min_element() > 0.);
        if outputs.is_empty() {
            return None;
        }
        outputs.sort_by(|a, b| a.name.cmp(&b.name));
        let origin = outputs.iter().fold(DVec2::splat(f64::INFINITY), |v, o| {
            v.min(o.position.as_dvec2())
        });
        let end = outputs
            .iter()
            .fold(DVec2::splat(f64::NEG_INFINITY), |v, o| {
                v.max((o.position + o.size).as_dvec2())
            });
        Some(Self {
            outputs,
            origin,
            extent: end - origin,
        })
    }
}

#[derive(Default)]
pub struct Feed {
    pending: Arc<Mutex<Option<Layout>>>,
    stop: Arc<AtomicBool>,
    worker: Option<JoinHandle<()>>,
}
impl Feed {
    pub fn start() -> Self {
        let mut feed = Self::default();
        #[cfg(feature = "wayland")]
        {
            let pending = feed.pending.clone();
            let stop = feed.stop.clone();
            feed.worker = std::thread::Builder::new()
                .name("desktop-layout".into())
                .spawn(move || {
                    let Some(mut client) = wlx_capture::wayland::WlxClient::new() else {
                        return;
                    };
                    let mut previous = None;
                    while !stop.load(Ordering::Relaxed) {
                        let _ = client.connection.flush();
                        client.dispatch_pending();
                        // Geometry is read from the output map; discard its notification queue.
                        client.iter_events().for_each(drop);
                        let snapshot = Layout::new(
                            client
                                .outputs
                                .values()
                                .map(|o| Output {
                                    name: o.name.to_string(),
                                    position: Vec2::new(
                                        o.logical_pos.0 as f32,
                                        o.logical_pos.1 as f32,
                                    ),
                                    size: Vec2::new(
                                        o.logical_size.0 as f32,
                                        o.logical_size.1 as f32,
                                    ),
                                })
                                .collect(),
                        );
                        if snapshot.is_some() && snapshot != previous {
                            *pending.lock().unwrap() = snapshot.clone();
                            previous = snapshot;
                        }
                        std::thread::park_timeout(Duration::from_millis(100));
                    }
                })
                .ok();
        }
        feed
    }
    pub fn take_update(&mut self) -> Option<Layout> {
        self.pending.lock().unwrap().take()
    }
}
impl Drop for Feed {
    fn drop(&mut self) {
        self.stop.store(true, Ordering::Relaxed);
        if let Some(worker) = self.worker.take() {
            worker.thread().unpark();
            let _ = worker.join();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn output(name: &str, x: f32, y: f32, w: f32, h: f32) -> Output {
        Output {
            name: name.into(),
            position: Vec2::new(x, y),
            size: Vec2::new(w, h),
        }
    }
    #[test]
    #[ignore = "requires an active Wayland desktop"]
    fn live_output_subscription_reports_current_bounds() {
        let mut feed = Feed::start();
        let deadline = std::time::Instant::now() + Duration::from_secs(3);
        let expected = std::env::var("WIVRN_DESKTOP_LAYOUT_EXPECTED").unwrap();
        let values = expected
            .split(',')
            .map(|v| v.parse::<f64>().unwrap())
            .collect::<Vec<_>>();
        loop {
            if let Some(layout) = feed.take_update() {
                eprintln!("Live desktop: {:?}", layout);
                if layout.extent == DVec2::new(values[0], values[1]) {
                    return;
                }
            }
            assert!(
                std::time::Instant::now() < deadline,
                "live desktop extent did not match compositor output geometry"
            );
            std::thread::sleep(Duration::from_millis(20));
        }
    }
    #[test]
    fn virtual_monitor_changes_absolute_mouse_normalization() {
        let screens = vec![
            output("DP-6", 0., 0., 2560., 1080.),
            output("DP-5", 2560., 0., 2560., 1080.),
        ];
        let before = Layout::new(screens.clone()).unwrap();
        let mut after = screens;
        after.push(output("Virtual-1", 5120., 0., 1024., 768.));
        let after = Layout::new(after).unwrap();
        assert_eq!(before.extent, DVec2::new(5120., 1080.));
        assert_eq!(after.extent, DVec2::new(6144., 1080.));
        let target = DVec2::new(3840., 540.);
        let stale = (target - before.origin) / before.extent;
        assert_eq!(stale * after.extent + after.origin, DVec2::new(4608., 540.));
        let corrected = (target - after.origin) / after.extent;
        assert_eq!(corrected * after.extent + after.origin, target);
    }
    #[test]
    fn logical_layout_handles_negative_origins_scale_and_removed_outputs() {
        let layout = Layout::new(vec![
            output("left", -1920., -200., 1920., 1080.),
            output("scaled", 0., 0., 1280., 720.),
        ])
        .unwrap();
        assert_eq!(layout.origin, DVec2::new(-1920., -200.));
        assert_eq!(layout.extent, DVec2::new(3200., 1080.));
        let remaining = Layout::new(vec![layout.outputs[1].clone()]).unwrap();
        assert_eq!(remaining.extent, DVec2::new(1280., 720.));
        assert!(Layout::new(vec![output("not-ready", 0., 0., 0., 0.)]).is_none());
    }
}
