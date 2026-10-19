// SPDX-License-Identifier: GPL-3.0-only
//! Room placements share WiVRn's existing read-only, timestamped spatial bridge.
//! Socket work runs off the render thread; all poses expire at consumption time.
use glam::{Affine3A, Quat, Vec3};
use serde::Deserialize;
use std::{
    collections::BTreeMap,
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    path::Path,
    sync::{Arc, Mutex, mpsc},
    time::Duration,
};
use wlx_common::windowing::RoomBinding;

const LIMIT: usize = 128 * 1024;
const TTL: i64 = 750_000_000;
const SETTLE: i64 = 2_000_000_000;

pub fn now_ns() -> i64 {
    let mut t = libc::timespec {
        tv_sec: 0,
        tv_nsec: 0,
    };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut t) } != 0 {
        return i64::MAX;
    }
    t.tv_sec
        .saturating_mul(1_000_000_000)
        .saturating_add(t.tv_nsec)
}

fn uuid_valid(s: &str) -> bool {
    s.len() == 32
        && s.bytes()
            .all(|b| b.is_ascii_hexdigit() && !b.is_ascii_uppercase())
        && s.bytes().any(|b| b != b'0')
}

fn transform_valid(t: Affine3A) -> bool {
    t.to_cols_array().iter().all(|v| v.is_finite())
        && t.translation.length() < 100.0
        && t.matrix3.determinant().is_finite()
        && t.matrix3.determinant() > 0.000001
        && t.matrix3.x_axis.length() < 100.0
        && t.matrix3.y_axis.length() < 100.0
        && t.matrix3.z_axis.length() < 100.0
}

#[derive(Clone, Deserialize)]
struct Pose {
    position: [f32; 3],
    rotation: [f32; 4],
}
impl Pose {
    fn transform(&self) -> Option<Affine3A> {
        let p = Vec3::from(self.position);
        let q = Quat::from_array(self.rotation);
        (p.is_finite()
            && p.length() < 100.0
            && q.is_finite()
            && (q.length_squared() - 1.0).abs() < 0.02)
            .then(|| Affine3A::from_rotation_translation(q.normalize(), p))
    }
}
#[derive(Clone, Deserialize)]
struct Location {
    valid: bool,
    flags: u64,
    quest_stage: Option<Pose>,
    pc_stage: Option<Pose>,
}
impl Location {
    fn poses(&self) -> Option<(Affine3A, Affine3A)> {
        if !self.valid || self.flags & 15 != 15 {
            return None;
        }
        Some((
            self.quest_stage.as_ref()?.transform()?,
            self.pc_stage.as_ref()?.transform()?,
        ))
    }
}
#[derive(Clone, Deserialize)]
struct Marker {
    uuid: String,
    persisted: bool,
    pose: Location,
}
#[derive(Clone, Deserialize)]
struct Frame {
    schema: String,
    source_session: String,
    sequence: u64,
    generation: u64,
    sample_monotonic_ns: i64,
    live: bool,
    head: Location,
    markers: Vec<Marker>,
}

struct StableAnchor {
    baseline: Affine3A,
    pc: Affine3A,
    since: i64,
    alignment_baseline: Affine3A,
    alignment_since: i64,
}
#[derive(Default)]
struct Reference {
    epoch: Option<(u64, String, u64)>,
    revision: u64,
    sequence: u64,
    sample: i64,
    not_before: i64,
    blocked_sequence: u64,
    head: Option<Affine3A>,
    anchors: BTreeMap<String, StableAnchor>,
}
impl Reference {
    fn clear(&mut self) {
        self.anchors.clear();
        self.head = None;
    }
    fn ingest(&mut self, connection: u64, f: &Frame, now: i64) {
        if f.schema != "wivrn.spatial.v1"
            || f.source_session.is_empty()
            || f.source_session == "0"
            || f.sequence == 0
            || f.generation == 0
            || f.markers.len() > 12
        {
            self.clear();
            return;
        }
        let epoch = (connection, f.source_session.clone(), f.generation);
        if self.epoch.as_ref() != Some(&epoch) {
            self.clear();
            self.epoch = Some(epoch);
            self.revision += 1;
            self.sequence = 0;
            self.sample = 0;
            self.not_before = 0;
            self.blocked_sequence = 0;
        }
        let age = now.saturating_sub(f.sample_monotonic_ns);
        if !f.live || !(-100_000_000..=TTL).contains(&age) || f.head.poses().is_none() {
            self.clear();
            return;
        }
        if f.sequence < self.sequence || f.sample_monotonic_ns < self.sample {
            self.clear();
            return;
        }
        // Idle bridge heartbeats repeat a sample. They cannot extend its lifetime.
        if f.sequence == self.sequence {
            return;
        }
        if f.sample_monotonic_ns.saturating_sub(self.sample) > TTL {
            self.clear();
        }
        self.sequence = f.sequence;
        self.sample = f.sample_monotonic_ns;
        self.head = f.head.poses().map(|(_, pc)| pc);
        let mut seen = std::collections::BTreeSet::new();
        for marker in &f.markers {
            if !marker.persisted || !uuid_valid(&marker.uuid) {
                continue;
            }
            if !seen.insert(marker.uuid.clone()) {
                self.clear();
                return;
            }
            let Some((raw, pc)) = marker.pose.poses() else {
                self.anchors.remove(&marker.uuid);
                continue;
            };
            let alignment = pc * raw.inverse();
            let entry = self
                .anchors
                .entry(marker.uuid.clone())
                .or_insert(StableAnchor {
                    baseline: raw,
                    pc,
                    since: self.sample,
                    alignment_baseline: alignment,
                    alignment_since: self.sample,
                });
            // Judge localization in headset STAGE, so changing the PC floor does
            // not look like a moving physical anchor.
            let distance = (raw.translation - entry.baseline.translation).length();
            let angle = Quat::from_affine3(&raw).angle_between(Quat::from_affine3(&entry.baseline));
            if distance > 0.02 || angle > 1.0f32.to_radians() {
                entry.baseline = raw;
                entry.since = self.sample;
            }
            if (alignment.translation - entry.alignment_baseline.translation).length() > 0.005
                || Quat::from_affine3(&alignment)
                    .angle_between(Quat::from_affine3(&entry.alignment_baseline))
                    > 0.5f32.to_radians()
            {
                entry.alignment_baseline = alignment;
                entry.alignment_since = self.sample;
            }
            entry.pc = pc;
        }
        self.anchors.retain(|id, _| seen.contains(id));
    }
    fn fresh(&self, now: i64) -> bool {
        self.head.is_some()
            && self.sample > self.not_before
            && self.sequence > self.blocked_sequence
            && (-100_000_000..=TTL).contains(&now.saturating_sub(self.sample))
    }
    fn anchor(&self, uuid: &str, now: i64) -> Option<Affine3A> {
        if !self.fresh(now) {
            return None;
        }
        let a = self.anchors.get(uuid)?;
        (self.sample.saturating_sub(a.since) >= SETTLE).then_some(a.pc)
    }
    fn bind(&self, world: Affine3A, now: i64) -> Option<RoomBinding> {
        if !transform_valid(world) {
            return None;
        }
        let (uuid, anchor) = self
            .anchors
            .iter()
            .filter_map(|(id, _)| self.anchor(id, now).map(|a| (id, a)))
            .min_by(|(_, a), (_, b)| {
                (a.translation - world.translation)
                    .length_squared()
                    .total_cmp(&(b.translation - world.translation).length_squared())
            })?;
        Some(RoomBinding {
            uuid: uuid.clone(),
            relative: anchor.inverse() * world,
        })
    }
}

#[derive(Default)]
struct Mailbox {
    connection: u64,
    frame: Option<Frame>,
}

#[derive(Default)]
pub struct Feed {
    pub floor_status: &'static str,
    reference: Reference,
    mailbox: Arc<Mutex<Mailbox>>,
    stop: Option<mpsc::Sender<()>>,
}
impl Feed {
    /// Called only by the OpenXR backend. Other backends cannot consume PC STAGE.
    pub fn start(&mut self) {
        if self.stop.is_some() {
            return;
        }
        let path = std::env::var_os("XDG_RUNTIME_DIR")
            .map(std::path::PathBuf::from)
            .unwrap_or_else(|| format!("/run/user/{}", unsafe { libc::getuid() }).into())
            .join("wivrn-pro/bridge.sock");
        self.start_at(path);
    }
    fn start_at(&mut self, path: std::path::PathBuf) {
        if self.stop.is_some() {
            return;
        }
        let mailbox = self.mailbox.clone();
        let (tx, rx) = mpsc::channel();
        match std::thread::Builder::new()
            .name("wayvr-room".into())
            .spawn(move || {
                while rx.try_recv() == Err(mpsc::TryRecvError::Empty) {
                    if let Ok(socket) = connect(&path) {
                        {
                            let mut m = mailbox.lock().unwrap();
                            m.connection += 1;
                            m.frame = None;
                        }
                        let mut bytes = vec![0u8; LIMIT];
                        loop {
                            if rx.try_recv() != Err(mpsc::TryRecvError::Empty) {
                                return;
                            }
                            let mut poll = libc::pollfd {
                                fd: socket.as_raw_fd(),
                                events: libc::POLLIN,
                                revents: 0,
                            };
                            let ready = unsafe { libc::poll(&mut poll, 1, 200) };
                            if ready == 0 {
                                continue;
                            }
                            if ready < 0
                                || poll.revents & (libc::POLLERR | libc::POLLHUP | libc::POLLNVAL)
                                    != 0
                            {
                                break;
                            }
                            let n = unsafe {
                                libc::recv(
                                    socket.as_raw_fd(),
                                    bytes.as_mut_ptr().cast(),
                                    bytes.len(),
                                    libc::MSG_TRUNC | libc::MSG_DONTWAIT,
                                )
                            };
                            if n <= 0 || n as usize > LIMIT {
                                break;
                            }
                            // Invalid/idle records immediately remove usable poses.
                            mailbox.lock().unwrap().frame =
                                serde_json::from_slice(&bytes[..n as usize]).ok();
                        }
                    }
                    mailbox.lock().unwrap().frame = None;
                    if rx.recv_timeout(Duration::from_millis(500))
                        != Err(mpsc::RecvTimeoutError::Timeout)
                    {
                        return;
                    }
                }
            }) {
            Ok(_) => self.stop = Some(tx),
            Err(e) => log::warn!("Could not start room anchor reader: {e}"),
        }
    }
    pub fn tick(&mut self) {
        let m = self.mailbox.lock().unwrap();
        if let Some(f) = &m.frame {
            self.reference.ingest(m.connection, f, now_ns());
        } else {
            self.reference.clear();
        }
    }
    pub fn revision(&self) -> u64 {
        self.reference.revision
    }
    pub fn anchor(&self, uuid: &str) -> Option<Affine3A> {
        self.reference.anchor(uuid, now_ns())
    }
    pub fn bind(&self, world: Affine3A) -> Option<RoomBinding> {
        self.reference.bind(world, now_ns())
    }
    pub fn locate(&self, binding: &RoomBinding) -> Option<Affine3A> {
        if !uuid_valid(&binding.uuid) || !transform_valid(binding.relative) {
            return None;
        }
        let world = self.anchor(&binding.uuid)? * binding.relative;
        transform_valid(world).then_some(world)
    }
    /// The old bridge sample is in the old PC STAGE. Wait for a new one before
    /// rendering or using placements after a successful Monado offset write.
    pub fn stage_changed(&mut self) {
        self.reference.not_before = now_ns().saturating_add(100_000_000);
        self.reference.blocked_sequence = self.reference.sequence;
    }
    pub fn remember_floor(&self) -> Option<RoomBinding> {
        if !self.reference.fresh(now_ns()) {
            return None;
        }
        let head = self.reference.head?;
        let binding = self.bind(Affine3A::from_translation(Vec3::new(
            head.translation.x,
            0.0,
            head.translation.z,
        )))?;
        self.locate_floor(&binding)?;
        Some(binding)
    }
    pub fn locate_floor(&self, binding: &RoomBinding) -> Option<Affine3A> {
        let world = self.locate(binding)?;
        let anchor = self.reference.anchors.get(&binding.uuid)?;
        (self.reference.sample.saturating_sub(anchor.alignment_since) >= SETTLE).then_some(world)
    }
}
impl Drop for Feed {
    fn drop(&mut self) {
        self.stop.take();
    }
}

/// Restore height only. Never tilt the runtime to match noisy room localization.
pub fn floor_height(world: Affine3A) -> Option<f32> {
    if !transform_valid(world) {
        return None;
    }
    let up = world.transform_vector3(Vec3::Y).normalize();
    (up.dot(Vec3::Y) > 5.0f32.to_radians().cos()).then_some(world.translation.y)
}

/// Monado stores root_from_stage: move that origin onto the physical floor.
pub fn floor_offset(before: Affine3A, floor: Affine3A) -> Option<Affine3A> {
    if !transform_valid(before) {
        return None;
    }
    let height = floor_height(floor)?;
    if height.abs() > 2.0 {
        return None;
    }
    let mut after = before;
    after.translation += before.transform_vector3a(glam::Vec3A::Y * height);
    Some(after)
}

fn connect(path: &Path) -> std::io::Result<OwnedFd> {
    let fd = unsafe {
        libc::socket(
            libc::AF_UNIX,
            libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC | libc::SOCK_NONBLOCK,
            0,
        )
    };
    if fd < 0 {
        return Err(std::io::Error::last_os_error());
    }
    let socket = unsafe { OwnedFd::from_raw_fd(fd) };
    let mut address: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    address.sun_family = libc::AF_UNIX as _;
    use std::os::unix::ffi::OsStrExt;
    let bytes = path.as_os_str().as_bytes();
    if bytes.len() >= address.sun_path.len() {
        return Err(std::io::ErrorKind::InvalidInput.into());
    }
    for (dst, src) in address.sun_path.iter_mut().zip(bytes) {
        *dst = *src as _;
    }
    let result = unsafe {
        libc::connect(
            fd,
            (&address as *const libc::sockaddr_un).cast(),
            std::mem::size_of_val(&address) as _,
        )
    };
    if result != 0 {
        return Err(std::io::Error::last_os_error());
    }
    let mut peer: libc::ucred = unsafe { std::mem::zeroed() };
    let mut size = std::mem::size_of_val(&peer) as libc::socklen_t;
    if unsafe {
        libc::getsockopt(
            fd,
            libc::SOL_SOCKET,
            libc::SO_PEERCRED,
            (&mut peer as *mut libc::ucred).cast(),
            &mut size,
        )
    } != 0
        || peer.uid != unsafe { libc::getuid() }
    {
        return Err(std::io::ErrorKind::PermissionDenied.into());
    }
    Ok(socket)
}

#[cfg(test)]
#[path = "room_tests.rs"]
mod tests;
