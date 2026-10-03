// SPDX-License-Identifier: GPL-3.0-only
//! Native consumer of the existing Shora status/battery hook. No CLI subprocesses.
use serde::Deserialize;
use std::{
    io::{BufRead, BufReader, Write},
    os::{fd::AsRawFd, unix::net::UnixStream},
    path::Path,
    sync::{Arc, RwLock, mpsc},
    time::{Duration, Instant},
};

const LIMIT: u64 = 2 * 1024 * 1024;
const MAX_AGE: Duration = Duration::from_secs(3);
#[path = "shora_reset.rs"]
mod reset;
pub use reset::ResetKind;
#[derive(Clone, Deserialize)]
struct Tracker {
    name: String,
    mac: String,
    sensor_id: u8,
    active: bool,
    battery_percent: Option<f32>,
}
#[derive(Clone, Deserialize)]
struct Status {
    pid: u32,
    paused: bool,
    trackers: Vec<Tracker>,
}
#[derive(Deserialize)]
struct Reply {
    ok: bool,
    error: Option<String>,
    status: Option<Status>,
}
#[derive(Default)]
struct State {
    sample: Option<(Instant, Status)>,
}

fn connect(path: &Path) -> anyhow::Result<BufReader<UnixStream>> {
    let stream = UnixStream::connect(path)?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_millis(250)))?;
    let mut peer: libc::ucred = unsafe { std::mem::zeroed() };
    let mut len = std::mem::size_of_val(&peer) as libc::socklen_t;
    let rc = unsafe {
        libc::getsockopt(
            stream.as_raw_fd(),
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            &mut peer as *mut _ as *mut _,
            &mut len,
        )
    };
    anyhow::ensure!(
        rc == 0 && peer.uid == unsafe { libc::getuid() },
        "Shora peer UID mismatch"
    );
    Ok(BufReader::new(stream))
}

fn exchange(reader: &mut BufReader<UnixStream>, command: &str) -> anyhow::Result<Status> {
    writeln!(reader.get_mut(), "{}", serde_json::json!({"command": command}))?;
    let mut line = Vec::new();
    use std::io::Read;
    let mut bounded = reader.take(LIMIT);
    bounded.read_until(b'\n', &mut line)?;
    anyhow::ensure!(
        line.last() == Some(&b'\n'),
        "Incomplete or oversized Shora status"
    );
    let reply: Reply = serde_json::from_slice(&line)?;
    anyhow::ensure!(reply.ok, "{}", reply.error.as_deref().unwrap_or("Tracking command rejected"));
    let mut status = reply
        .status
        .ok_or_else(|| anyhow::anyhow!("Missing Shora status"))?;
    anyhow::ensure!(
        status.pid > 0 && status.trackers.len() <= 256,
        "Invalid Shora status"
    );
    status.trackers.sort_by(|a, b| {
        a.name
            .cmp(&b.name)
            .then(a.mac.cmp(&b.mac))
            .then(a.sensor_id.cmp(&b.sensor_id))
    });
    Ok(status)
}

fn read_status(path: &Path) -> anyhow::Result<Status> {
    exchange(&mut connect(path)?, "status")
}

/// The render thread only reads this cache. Missing/stalled producers expire visibly.
pub struct BatteryFeed {
    reset: reset::ResetControl,
    state: Arc<RwLock<State>>,
    stop: Option<mpsc::Sender<()>>,
    widget_keys: Vec<String>,
    next_widget_check: Instant,
}
#[derive(Debug, PartialEq)]
pub struct BatteryTile {
    pub key: String,
    pub charge: Option<f32>,
}

fn battery_tiles(sample: Option<&(Instant, Status)>) -> Vec<BatteryTile> {
    let Some((at, status)) = sample else {
        return vec![];
    };
    let live = at.elapsed() <= MAX_AGE && !status.paused;
    let mut seen = std::collections::HashSet::new();
    status
        .trackers
        .iter()
        .filter_map(|t| {
            // Only battery-reporting physical devices. HaritoraX's synthesized knee
            // trackers have no battery and should not become misleading empty tiles.
            let percent = t
                .battery_percent
                .filter(|p| p.is_finite() && (0.0..=100.0).contains(p))?;
            // A SlimeVR device may export multiple sensors but has one battery.
            let key = t.mac.clone();
            if !seen.insert(key.clone()) {
                return None;
            }
            Some(BatteryTile {
                key,
                charge: (live && t.active).then_some(percent / 100.0),
            })
        })
        .collect()
}
impl BatteryFeed {
    pub fn request_reset(&self, kind: ResetKind) { self.reset.request(kind); }
    pub fn reset_text(&self) -> String { self.reset.text() }
    pub fn new() -> Self {
        let state = Arc::new(RwLock::new(State::default()));
        let reset = reset::ResetControl::new(std::env::var_os("WIVRN_SHORA_CONTROL_SOCKET").map(Into::into));
        let Some(path) = std::env::var_os("WIVRN_SHORA_CONTROL_SOCKET") else {
            return Self {
                reset,
                state,
                stop: None,
                widget_keys: vec![],
                next_widget_check: Instant::now(),
            };
        };
        let shared = state.clone();
        let (tx, rx) = mpsc::channel();
        let worker = std::thread::Builder::new().name("shora-batteries".into()).spawn(move || {
          let mut previous = None;
          loop {
            let sample = read_status(Path::new(&path)).ok().map(|status|(Instant::now(),status));
            let current = sample.as_ref().map(|(_,s)|(s.pid,s.trackers.len(),s.trackers.iter().filter(|t|t.battery_percent.is_some()).count()));
            if current != previous {
                if let Some((pid, trackers, batteries)) = current {log::info!("Shora battery hook connected: PID {pid}, {trackers} trackers, {batteries} battery reports");}
                else {log::warn!("Shora battery hook disconnected; clearing displayed values");}
                previous = current;
            }
            // Retain identities on disconnection, but expire their percentages.
            if sample.is_some() {shared.write().unwrap().sample = sample;}
            if rx.recv_timeout(Duration::from_millis(500)) != Err(mpsc::RecvTimeoutError::Timeout) {break;}
          }
        });
        if let Err(error) = worker {
            log::warn!("Could not start Shora battery reader: {error}");
        }
        Self {
            reset,
            state,
            stop: Some(tx),
            widget_keys: vec![],
            next_widget_check: Instant::now(),
        }
    }
    pub fn tiles(&self) -> Vec<BatteryTile> {
        battery_tiles(self.state.read().unwrap().sample.as_ref())
    }
    pub fn tile_charge(&self, key: &str) -> Option<f32> {
        self.tiles()
            .into_iter()
            .find(|t| t.key == key)
            .and_then(|t| t.charge)
    }
    pub fn layout_changed(&mut self) -> bool {
        if Instant::now() < self.next_widget_check {
            return false;
        }
        self.next_widget_check = Instant::now() + Duration::from_millis(500);
        let keys = self.tiles().into_iter().map(|t| t.key).collect::<Vec<_>>();
        if self.widget_keys == keys {
            return false;
        }
        self.widget_keys = keys;
        true
    }
    pub fn text(&self, limit: usize) -> (String, bool) {
        if self.stop.is_none() {
            return (String::new(), false);
        }
        let state = self.state.read().unwrap();
        summary(
            state
                .sample
                .as_ref()
                .filter(|(at, _)| at.elapsed() <= MAX_AGE)
                .map(|(_, s)| s),
            limit,
        )
    }
}
impl Drop for BatteryFeed {
    fn drop(&mut self) {
        if let Some(stop) = self.stop.take() {
            let _ = stop.send(());
        }
    }
}

fn summary(status: Option<&Status>, limit: usize) -> (String, bool) {
    let Some(status) = status else {
        return ("Shora batteries: disconnected / stale".into(), false);
    };
    let active = status.trackers.iter().filter(|t| t.active).count();
    let mut text = format!(
        "Shora {}: {active}/{} active",
        if status.paused { "paused" } else { "trackers" },
        status.trackers.len()
    );
    let mut low = false;
    for (i, t) in status.trackers.iter().take(limit).enumerate() {
        let name: String = t
            .name
            .split_whitespace()
            .collect::<Vec<_>>()
            .join(" ")
            .chars()
            .take(if limit <= 6 { 12 } else { 20 })
            .collect();
        let known = t
            .battery_percent
            .filter(|v| v.is_finite() && (0.0..=100.0).contains(v));
        let value = if !t.active {
            "offline".into()
        } else if let Some(v) = known {
            if v <= 20.0 {
                low = true;
                format!("{v:.0}% LOW")
            } else {
                format!("{v:.0}%")
            }
        } else {
            "unknown".into()
        };
        text.push_str(if i % 2 == 0 { "\n" } else { "   |   " });
        text.push_str(&format!("{name}: {value}"));
    }
    if status.trackers.len() > limit {
        text.push_str(&format!(
            "\n+{} more — open WiVRn dashboard",
            status.trackers.len() - limit
        ));
    }
    (text, low)
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn tiles_represent_physical_batteries_and_expire_without_losing_identity() {
        let mut status = Status {
            pid: 1,
            paused: false,
            trackers: vec![],
        };
        for (mac, sensor_id, active, battery_percent) in [
            ("chest", 0, true, Some(100.0)),
            ("chest", 1, true, Some(100.0)),
            ("hip", 0, true, Some(20.0)),
            ("left", 0, true, Some(0.0)),
            ("right", 0, false, Some(90.0)),
            ("virtual-knee", 0, true, None),
            ("invalid", 0, true, Some(101.0)),
            ("nan", 0, true, Some(f32::NAN)),
        ] {
            status.trackers.push(Tracker {
                name: mac.into(),
                mac: mac.into(),
                sensor_id,
                active,
                battery_percent,
            });
        }
        let mut sample = (Instant::now(), status);
        let tiles = battery_tiles(Some(&sample));
        assert_eq!(tiles.len(), 4);
        assert_eq!(
            tiles.iter().map(|t| t.charge).collect::<Vec<_>>(),
            vec![Some(1.0), Some(0.2), Some(0.0), None]
        );
        sample.1.paused = true;
        assert!(
            battery_tiles(Some(&sample))
                .iter()
                .all(|t| t.charge.is_none())
        );
        sample.1.paused = false;
        sample.0 = Instant::now() - MAX_AGE - Duration::from_secs(1);
        let stale = battery_tiles(Some(&sample));
        assert_eq!(
            stale.iter().map(|t| &t.key).collect::<Vec<_>>(),
            tiles.iter().map(|t| &t.key).collect::<Vec<_>>()
        );
        assert!(stale.iter().all(|t| t.charge.is_none()));
        assert!(battery_tiles(None).is_empty());
    }

    #[test]
    fn only_membership_changes_rebuild_the_device_layout() {
        let state = Arc::new(RwLock::new(State {
            sample: Some((
                Instant::now(),
                Status {
                    pid: 1,
                    paused: false,
                    trackers: vec![Tracker {
                        name: "hip".into(),
                        mac: "hip".into(),
                        sensor_id: 0,
                        active: true,
                        battery_percent: Some(80.0),
                    }],
                },
            )),
        }));
        let mut feed = BatteryFeed {
            reset: reset::ResetControl::new(None),
            state: state.clone(),
            stop: None,
            widget_keys: vec![],
            next_widget_check: Instant::now(),
        };
        assert!(feed.layout_changed());
        state.write().unwrap().sample.as_mut().unwrap().1.trackers[0].battery_percent = Some(70.0);
        feed.next_widget_check = Instant::now();
        assert!(!feed.layout_changed());
        assert_eq!(feed.tile_charge("hip"), Some(0.7));
        state
            .write()
            .unwrap()
            .sample
            .as_mut()
            .unwrap()
            .1
            .trackers
            .clear();
        feed.next_widget_check = Instant::now();
        assert!(feed.layout_changed());
    }

    #[test]
    fn known_unknown_offline_low_and_overflow_match_existing_hooks() {
        let mut status = Status {
            pid: 1,
            paused: false,
            trackers: vec![],
        };
        for (name, active, battery) in [
            ("hip", true, Some(75.0)),
            ("foot", true, Some(20.0)),
            ("extension", true, None),
            ("lost", false, Some(90.0)),
            ("bad", true, Some(110.0)),
        ] {
            status.trackers.push(Tracker {
                name: name.into(),
                mac: String::new(),
                sensor_id: 0,
                active,
                battery_percent: battery,
            });
        }
        let (text, low) = summary(Some(&status), 16);
        for expected in [
            "hip: 75%",
            "foot: 20% LOW",
            "extension: unknown",
            "lost: offline",
            "bad: unknown",
        ] {
            assert!(text.contains(expected));
        }
        assert!(low);
        assert!(summary(Some(&status), 2).0.contains("+3 more"));
        assert!(summary(None, 16).0.contains("disconnected / stale"));
    }
    #[test]
    fn stale_cache_never_keeps_old_percentages_visible() {
        let (tx, _rx) = mpsc::channel();
        let state = State {
            sample: Some((
                Instant::now() - MAX_AGE - Duration::from_secs(1),
                Status {
                    pid: 1,
                    paused: false,
                    trackers: vec![],
                },
            )),
        };
        let feed = BatteryFeed {
            reset: reset::ResetControl::new(None),
            state: Arc::new(RwLock::new(state)),
            stop: Some(tx),
            widget_keys: vec![],
            next_widget_check: Instant::now(),
        };
        assert!(feed.text(16).0.contains("stale"));
    }
    #[test]
    fn actual_control_socket_read_is_bounded_and_sorted() {
        use std::os::unix::net::UnixListener;
        let path =
            std::env::temp_dir().join(format!("wayvr-shora-test-{}.sock", std::process::id()));
        let listener = UnixListener::bind(&path).unwrap();
        let thread = std::thread::spawn(move || {
            for payload in [
                b"{\"ok\":true,\"status\":{\"pid\":1,\"paused\":false,\"trackers\":[]}}\n"
                    .as_slice(),
                b"{\"ok\":true}",
            ] {
                let (mut client, _) = listener.accept().unwrap();
                let mut command = String::new();
                BufReader::new(&client).read_line(&mut command).unwrap();
                assert_eq!(command, "{\"command\":\"status\"}\n");
                client.write_all(payload).unwrap();
            }
        });
        assert_eq!(read_status(&path).unwrap().pid, 1);
        assert!(read_status(&path).is_err());
        thread.join().unwrap();
        std::fs::remove_file(path).unwrap();
    }
}
