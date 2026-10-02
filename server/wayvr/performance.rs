// SPDX-License-Identifier: GPL-3.0-or-later
//! Cached local telemetry. No filesystem or sysfs work on WayVR's render thread.
use serde::Deserialize;
use std::{
    fs::{self, OpenOptions},
    io::Read,
    os::unix::fs::{MetadataExt, OpenOptionsExt},
    path::Path,
    sync::{Arc, RwLock, mpsc},
    time::{Duration, Instant},
};

const MAX_AGE_NS: i64 = 2_000_000_000;
const LIMIT: u64 = 64 * 1024;
pub fn monotonic_ns() -> i64 {
    let mut ts = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    unsafe {
        libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts);
    }
    ts.tv_sec * 1_000_000_000 + ts.tv_nsec
}
fn fresh(at: i64, now: i64) -> bool {
    at > 0 && at <= now && now - at <= MAX_AGE_NS
}
fn metric(v: f32) -> bool {
    v.is_finite() && (0.0..=2000.0).contains(&v)
}
#[derive(Clone, Deserialize)]
pub struct App {
    pub id: u64,
    pub completed: u64,
    pub updated_ns: i64,
    pub period_ms: f32,
    pub interval_ms: f32,
    pub cpu_ms: f32,
    pub gpu_ms: f32,
}
#[derive(Clone, Deserialize)]
pub struct Stream {
    pub id: u8,
    pub updated_ns: i64,
    pub frames: u64,
    pub repeats: u64,
    pub encode_ms: Option<f32>,
    pub delivery_ms: Option<f32>,
    pub decode_ms: Option<f32>,
    pub encode_display_ms: Option<f32>,
}
#[derive(Clone, Deserialize)]
pub struct Snapshot {
    pub version: u8,
    pub pid: u32,
    pub session: i64,
    pub updated_ns: i64,
    pub target_hz: f32,
    pub apps: Vec<App>,
    pub streams: Vec<Stream>,
}
impl Snapshot {
    fn validate(&self, now: i64) -> bool {
        self.version == 1
            && self.pid > 0
            && self.session > 0
            && self.session <= self.updated_ns
            && fresh(self.updated_ns, now)
            && metric(self.target_hz)
            && self.target_hz <= 240.0
            && self.apps.len() <= 32
            && self.streams.len() <= 8
            && self.apps.iter().all(|a| {
                a.id > 0
                    && a.updated_ns >= 0
                    && a.updated_ns <= self.updated_ns
                    && [a.period_ms, a.interval_ms, a.cpu_ms, a.gpu_ms]
                        .into_iter()
                        .all(metric)
            })
            && self.streams.iter().all(|s| {
                s.id < 8
                    && s.updated_ns >= 0
                    && s.updated_ns <= self.updated_ns
                    && [s.encode_ms, s.delivery_ms, s.decode_ms, s.encode_display_ms]
                        .into_iter()
                        .flatten()
                        .all(metric)
            })
    }
    pub fn busiest(&self, now: i64) -> Option<&App> {
        self.apps
            .iter()
            .filter(|a| fresh(a.updated_ns, now) && a.completed > 1 && a.interval_ms > 0.0)
            .max_by(|a, b| {
                a.cpu_ms
                    .max(a.gpu_ms)
                    .total_cmp(&b.cpu_ms.max(b.gpu_ms))
                    .then(b.id.cmp(&a.id))
            })
    }
}
fn read_snapshot(path: &Path, now: i64) -> anyhow::Result<Snapshot> {
    let f = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK)
        .open(path)?;
    let m = f.metadata()?;
    anyhow::ensure!(
        m.is_file()
            && m.uid() == unsafe { libc::getuid() }
            && m.mode() & 0o077 == 0
            && m.len() <= LIMIT,
        "unsafe or oversized performance snapshot"
    );
    let mut bytes = Vec::new();
    f.take(LIMIT + 1).read_to_end(&mut bytes)?;
    anyhow::ensure!(bytes.len() <= LIMIT as usize, "oversized snapshot");
    let sample: Snapshot = serde_json::from_slice(&bytes)?;
    anyhow::ensure!(sample.validate(now), "invalid/stale performance snapshot");
    Ok(sample)
}
#[derive(Clone, Default)]
struct Host {
    cpu_c: Option<f32>,
    gpu_c: Option<f32>,
    ram: Option<(f32, f32)>,
}
fn ram(text: &str) -> Option<(f32, f32)> {
    let get = |name: &str| {
        text.lines().find_map(|l| {
            l.strip_prefix(name)?
                .split_whitespace()
                .next()?
                .parse::<f32>()
                .ok()
        })
    };
    let total = get("MemTotal:")?;
    let available = get("MemAvailable:")?;
    (total.is_finite()
        && available.is_finite()
        && total > 0.0
        && available >= 0.0
        && available <= total)
        .then_some(((total - available) / 1048576.0, total / 1048576.0))
}
impl Host {
    fn read() -> Self {
        let mut host = Self {
            ram: fs::read_to_string("/proc/meminfo")
                .ok()
                .and_then(|s| ram(&s)),
            ..Self::default()
        };
        if let Ok(entries) = fs::read_dir("/sys/class/hwmon") {
            for entry in entries.flatten() {
                let path = entry.path();
                let name = fs::read_to_string(path.join("name")).unwrap_or_default();
                let slot = match name.trim() {
                    "coretemp" => &mut host.cpu_c,
                    "i915" | "xe" => &mut host.gpu_c,
                    _ => continue,
                };
                if let Ok(sensors) = fs::read_dir(path) {
                    for sensor in sensors.flatten() {
                        let name = sensor.file_name();
                        let name = name.to_string_lossy();
                        if !name.starts_with("temp") || !name.ends_with("_input") {
                            continue;
                        }
                        if let Some(c) = fs::read_to_string(sensor.path())
                            .ok()
                            .and_then(|s| s.trim().parse::<f32>().ok())
                            .map(|v| v / 1000.0)
                            .filter(|v| v.is_finite() && (0.0..=150.0).contains(v))
                        {
                            *slot = Some(slot.unwrap_or(c).max(c));
                        }
                    }
                }
            }
        }
        host
    }
    fn text(&self) -> String {
        let temperature = |v: Option<f32>| v.map_or_else(|| "--".into(), |v| format!("{v:.0} C"));
        let ram = self.ram.map_or_else(
            || "--".into(),
            |(used, total)| format!("{used:.1}/{total:.1} GiB"),
        );
        format!(
            "CPU {}   Intel GPU {}   RAM {ram}",
            temperature(self.cpu_c),
            temperature(self.gpu_c)
        )
    }
}
#[derive(Default)]
struct State {
    sample: Option<Snapshot>,
    host: Host,
    host_at: Option<Instant>,
}
pub struct Feed {
    state: Arc<RwLock<State>>,
    stop: mpsc::Sender<()>,
    pub mode: u8,
}
impl Feed {
    pub fn new() -> Self {
        let state = Arc::new(RwLock::new(State::default()));
        let shared = state.clone();
        let (tx, rx) = mpsc::channel();
        let path = std::env::var_os("XDG_RUNTIME_DIR")
            .map(|p| std::path::PathBuf::from(p).join("wivrn-pro/performance.json"));
        let result = std::thread::Builder::new()
            .name("performance".into())
            .spawn(move || {
                let mut next_host = Instant::now();
                loop {
                    let sample = path
                        .as_ref()
                        .and_then(|p| read_snapshot(p, monotonic_ns()).ok());
                    let host = if Instant::now() >= next_host {
                        next_host = Instant::now() + Duration::from_secs(1);
                        Some(Host::read())
                    } else {
                        None
                    };
                    {
                        let mut s = shared.write().unwrap();
                        s.sample = sample;
                        if let Some(host) = host {
                            s.host = host;
                            s.host_at = Some(Instant::now());
                        }
                    }
                    if rx.recv_timeout(Duration::from_millis(200))
                        != Err(mpsc::RecvTimeoutError::Timeout)
                    {
                        break;
                    }
                }
            });
        if let Err(e) = result {
            log::warn!("Performance sampler unavailable: {e}");
        }
        Self {
            state,
            stop: tx,
            mode: 0,
        }
    }
    pub fn snapshot(&self) -> Option<Snapshot> {
        self.state
            .read()
            .unwrap()
            .sample
            .as_ref()
            .filter(|s| fresh(s.updated_ns, monotonic_ns()))
            .cloned()
    }
    pub fn host_text(&self) -> String {
        let s = self.state.read().unwrap();
        if s.host_at
            .is_some_and(|at| at.elapsed() < Duration::from_secs(3))
        {
            s.host.text()
        } else {
            "Host sensors: unavailable / stale".into()
        }
    }
}
impl Drop for Feed {
    fn drop(&mut self) {
        let _ = self.stop.send(());
    }
}

pub fn summary(sample: Option<&Snapshot>, now: i64) -> String {
    let Some(s) = sample.filter(|s| fresh(s.updated_ns, now)) else {
        return "WiVRn telemetry: unavailable / stale".into();
    };
    let mut text = format!("Stream target {:.0} Hz", s.target_hz);
    if let Some(a) = s.busiest(now) {
        text.push_str(&format!("   Busiest XR #{}: {:.1} FPS\nCPU pacing {:.2} ms   GPU wait {:.2} ms   Budget {:.2} ms",
            a.id,1000.0/a.interval_ms,a.cpu_ms,a.gpu_ms,a.period_ms));
    } else {
        text.push_str("\nXR pacing: waiting for frames");
    }
    let streams: Vec<_> = s
        .streams
        .iter()
        .filter(|s| fresh(s.updated_ns, now))
        .collect();
    let max = |f: fn(&Stream) -> Option<f32>| {
        streams
            .iter()
            .filter_map(|s| f(s))
            .reduce(f32::max)
            .map_or_else(|| "--".into(), |v| format!("{v:.1}"))
    };
    text.push_str(&format!(
        "\nEncode {}   Delivery {}   Decode {} ms\nEncode to display {} ms (not motion-to-photon)",
        max(|s| s.encode_ms),
        max(|s| s.delivery_ms),
        max(|s| s.decode_ms),
        max(|s| s.encode_display_ms)
    ));
    if streams.is_empty() {
        text.push_str("\nStream feedback: unavailable / stale");
    } else {
        text.push_str("\nExtra displays / received frames:");
        for s in streams {
            text.push_str(&format!(" S{} {}/{}", s.id, s.repeats, s.frames));
        }
    }
    text
}

#[cfg(test)]
mod tests {
    use super::*;
    fn sample() -> Snapshot {
        serde_json::from_str(r#"{"version":1,"pid":42,"session":1,"updated_ns":3000000000,"target_hz":90,"apps":[{"id":1,"completed":30,"updated_ns":2999000000,"period_ms":11.11,"interval_ms":11.12,"cpu_ms":2.0,"gpu_ms":1.0}],"streams":[]}"#).unwrap()
    }
    #[test]
    fn expiration_and_source_validation() {
        let mut s = sample();
        assert!(s.validate(3_000_000_000));
        assert!(!s.validate(5_000_000_001));
        assert!(!s.validate(2_999_999_999));
        s.apps[0].cpu_ms = f32::NAN;
        assert!(!s.validate(3_000_000_000));
        s = sample();
        s.apps[0].updated_ns = 4_000_000_000;
        assert!(!s.validate(3_000_000_000));
        s = sample();
        s.version = 2;
        assert!(!s.validate(3_000_000_000));
    }
    #[test]
    fn inactive_pacers_are_not_reported_as_games() {
        let mut s = sample();
        let mut other = s.apps[0].clone();
        other.id = 2;
        other.cpu_ms = 10.0;
        s.apps.push(other);
        assert_eq!(s.busiest(3_000_000_000).unwrap().id, 2);
        s.apps[1].updated_ns = 1;
        assert_eq!(s.busiest(3_000_000_000).unwrap().id, 1);
        assert!(s.busiest(6_000_000_000).is_none());
        assert!(summary(Some(&s), 3_000_000_000).contains("Busiest XR #1"));
        assert!(summary(Some(&s), 6_000_000_000).contains("stale"));
    }
    #[test]
    fn ram_uses_available_not_free() {
        assert_eq!(
            ram("MemTotal: 4194304 kB\nMemFree: 1 kB\nMemAvailable: 1048576 kB\n"),
            Some((3.0, 4.0))
        );
        assert_eq!(ram("MemTotal: 10 kB\nMemAvailable: 20 kB"), None);
    }
    #[test]
    fn reader_rejects_symlinks_and_public_snapshots() {
        use std::os::unix::fs::{PermissionsExt, symlink};
        let dir = std::env::temp_dir().join(format!("wivrn-perf-test-{}", std::process::id()));
        fs::create_dir_all(&dir).unwrap();
        let path = dir.join("sample");
        fs::write(&path, "{}").unwrap();
        fs::set_permissions(&path, fs::Permissions::from_mode(0o644)).unwrap();
        assert!(read_snapshot(&path, monotonic_ns()).is_err());
        symlink(&path, dir.join("link")).unwrap();
        assert!(read_snapshot(&dir.join("link"), monotonic_ns()).is_err());
        fs::remove_dir_all(dir).unwrap();
    }
}
