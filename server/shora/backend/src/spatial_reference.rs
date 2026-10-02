//! Read-only reference health from WiVRn's spatial bridge. Never transforms body poses.
use std::{collections::BTreeMap, io, os::fd::{AsRawFd, OwnedFd}, path::Path, sync::{Arc, RwLock}, time::Duration};
use nalgebra::{Quaternion, UnitQuaternion, Vector3};
use serde::{Deserialize, Serialize};
use socket2::{Domain, SockAddr, Socket, Type};
use tokio::io::unix::AsyncFd;

const TTL: i64 = 750_000_000;
const SETTLE: i64 = 2_000_000_000;
const LIMIT: usize = 128 * 1024;
pub type Shared = Arc<RwLock<Reference>>;
pub fn now_ns() -> i64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    if unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) } != 0 { return i64::MAX; }
    ts.tv_sec.saturating_mul(1_000_000_000).saturating_add(ts.tv_nsec)
}
#[derive(Clone, Debug, Serialize)]
pub struct Health {
    pub ready: bool,
    pub revision: u64,
    pub state: &'static str,
    pub anchors: usize,
}
#[derive(Deserialize)]
struct Pose { position: [f32; 3], rotation: [f32; 4] }
impl Pose {
    fn checked(&self) -> Option<(Vector3<f32>, UnitQuaternion<f32>)> {
        if !self.position.iter().chain(self.rotation.iter()).all(|x| x.is_finite()) { return None; }
        let [x,y,z,w] = self.rotation;
        let q = Quaternion::new(w,x,y,z);
        if (q.norm_squared()-1.0).abs() > 0.02 { return None; }
        Some((Vector3::from(self.position), UnitQuaternion::new_normalize(q)))
    }
}
#[derive(Deserialize)]
struct Location { valid: bool, flags: u64, quest_stage: Option<Pose> }
#[derive(Deserialize)]
struct Marker { uuid: String, persisted: bool, pose: Location }
#[derive(Deserialize)]
struct Frame {
    schema: String, source_session: String, sequence: u64, generation: u64,
    sample_monotonic_ns: i64, live: bool, head: Location, markers: Vec<Marker>,
}
type Anchors = BTreeMap<String, (Vector3<f32>, UnitQuaternion<f32>)>;
pub struct Reference {
    revision: u64,
    epoch: Option<(String,u64)>,
    sequence: u64,
    sampled: i64,
    since: Option<i64>,
    baseline: Anchors,
    anchored: bool,
    reason: &'static str,
}
impl Default for Reference {
    fn default() -> Self { Self {revision:1, epoch:None, sequence:0, sampled:0, since:None,
        baseline:Anchors::new(), anchored:false, reason:"waiting_for_spatial_reference"} }
}
impl Reference {
    pub fn invalidate(&mut self, reason: &'static str) {
        if self.since.is_some() { self.revision += 1; }
        self.since = None; self.baseline.clear(); self.reason = reason;
    }
    pub fn health(&self, now: i64) -> Health {
        let age = now.saturating_sub(self.sampled);
        let fresh = (-100_000_000..=TTL).contains(&age);
        let ready = fresh && self.since.is_some_and(|at| now.saturating_sub(at) >= SETTLE);
        Health {ready, revision:self.revision, anchors:self.baseline.len(), state:
            if self.since.is_none() {self.reason} else if !fresh {"spatial_reference_stale"}
            else if !ready {"reference_settling"} else if self.anchored {"anchors_stable"} else {"tracking_only_no_anchor_validation"}}
    }
    pub fn ingest(&mut self, bytes: &[u8], now: i64) {
        // Expiry must break continuity even if the next packet arrives before the app polls.
        if self.since.is_some() && !(-100_000_000..=TTL).contains(&now.saturating_sub(self.sampled)) {
            self.invalidate("spatial_reference_stale");
        }
        let Ok(frame) = serde_json::from_slice::<Frame>(bytes) else { self.invalidate("invalid_spatial_record"); return; };
        if frame.schema != "wivrn.spatial.v1" || frame.source_session.parse::<u64>().ok().filter(|n| *n != 0).is_none()
            || frame.generation == 0 || frame.sequence == 0 || frame.markers.len() > 12 {
            self.invalidate("invalid_spatial_record"); return;
        }
        let epoch = (frame.source_session, frame.generation);
        if self.epoch.as_ref() != Some(&epoch) {
            self.invalidate("reference_changed"); self.revision += 1;
            self.epoch = Some(epoch); self.sequence = 0; self.sampled = 0;
        }
        if frame.sequence < self.sequence { self.invalidate("spatial_replay"); return; }
        if !frame.live || !frame.head.valid || frame.head.flags & 15 != 15
            || frame.head.quest_stage.as_ref().and_then(Pose::checked).is_none()
            || !(-100_000_000..=TTL).contains(&now.saturating_sub(frame.sample_monotonic_ns)) {
            self.invalidate("tracking_unavailable"); return;
        }
        if frame.sample_monotonic_ns < self.sampled { self.invalidate("spatial_time_reversed"); return; }
        if frame.sequence == self.sequence { return; } // Heartbeats cannot extend validity or settling.
        self.sequence = frame.sequence; self.sampled = frame.sample_monotonic_ns;
        let mut anchors = Anchors::new();
        for marker in frame.markers {
            if marker.persisted && marker.pose.valid && marker.pose.flags & 15 == 15 && marker.uuid.len() == 32
                && marker.uuid.bytes().all(|b| b.is_ascii_hexdigit()) && marker.uuid.bytes().any(|b| b != b'0') {
                if let Some(pose) = marker.pose.quest_stage.and_then(|p| p.checked()) {
                    if anchors.insert(marker.uuid, pose).is_some() {self.invalidate("duplicate_anchor"); return;}
                }
            }
        }
        if !self.anchored && anchors.len() >= 3 {
            self.invalidate("anchor_validation_started"); self.revision += 1; self.anchored = true;
        }
        if self.anchored {
            if anchors.len() < 3 {self.invalidate("anchors_unavailable"); return;}
            if !self.baseline.is_empty() {
                let common: Vec<_> = anchors.iter().filter_map(|(id,pose)| self.baseline.get(id).map(|base| (pose,base))).collect();
                if common.len() < 3 {self.invalidate("anchor_set_changed");}
                else if common.iter().any(|((p,q),(b,r))| (*p-*b).norm() > 0.05 || q.angle_to(r) > 3f32.to_radians()) {
                    self.invalidate("anchor_reference_moved");
                }
            }
            if self.baseline.is_empty() {self.baseline = anchors;}
        }
        self.since.get_or_insert(now);
    }
}
fn connect(path: &Path) -> io::Result<AsyncFd<OwnedFd>> {
    let socket = Socket::new(Domain::UNIX, Type::SEQPACKET, None)?;
    socket.set_nonblocking(true)?;
    socket.connect(&SockAddr::unix(path)?)?;
    let mut peer: libc::ucred = unsafe {std::mem::zeroed()};
    let mut len = std::mem::size_of_val(&peer) as libc::socklen_t;
    if unsafe {libc::getsockopt(socket.as_raw_fd(), libc::SOL_SOCKET, libc::SO_PEERCRED,
        &mut peer as *mut _ as *mut _, &mut len)} != 0 {return Err(io::Error::last_os_error());}
    if peer.uid != unsafe {libc::getuid()} {return Err(io::Error::new(io::ErrorKind::PermissionDenied,"spatial peer UID mismatch"));}
    AsyncFd::new(socket.into())
}
pub async fn run(path: std::path::PathBuf, shared: Shared) -> anyhow::Result<()> {
    let mut bytes = vec![0u8; LIMIT];
    loop {
        if let Ok(socket) = connect(&path) {
            loop {
                let Ok(mut ready) = socket.readable().await else { break; };
                let read = ready.try_io(|fd| {
                    let n = unsafe {libc::recv(fd.as_raw_fd(), bytes.as_mut_ptr() as *mut _, bytes.len(), libc::MSG_TRUNC)};
                    if n < 0 {Err(io::Error::last_os_error())} else {Ok(n as usize)}
                });
                match read {
                    Ok(Ok(n)) if n > 0 && n <= LIMIT => shared.write().unwrap().ingest(&bytes[..n], now_ns()),
                    Err(_) => continue,
                    _ => break,
                }
            }
        }
        shared.write().unwrap().invalidate("spatial_bridge_disconnected");
        tokio::time::sleep(Duration::from_millis(500)).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::{json, Value};
    pub fn frame(seq: u64, now: i64, count: usize) -> Value {
        let location = json!({"valid":true,"flags":15,"quest_stage":{"position":[0,1,0],"rotation":[0,0,0,1]}});
        json!({"schema":"wivrn.spatial.v1","source_session":"1","sequence":seq,"generation":1,
            "sample_monotonic_ns":now,"live":true,"head":location,"markers":(0..count).map(|i|
                json!({"uuid":format!("{:032x}",i+1),"persisted":true,"pose":location})).collect::<Vec<_>>()})
    }
    pub fn feed(r: &mut Reference, f: &Value, now: i64) {r.ingest(&serde_json::to_vec(f).unwrap(),now);}
    fn settle(r: &mut Reference, start: i64, seq: u64, count: usize) -> i64 {
        for i in 0..=10 {let now=start+i*200_000_000; feed(r,&frame(seq+i as u64,now,count),now);}
        start+SETTLE
    }
    #[test]
    fn needs_continuous_fresh_packets_and_head_tracking() {
        let mut r=Reference::default(); let now=settle(&mut r,10_000_000_000,1,0);
        assert!(r.health(now).ready); assert_eq!(r.health(now).state,"tracking_only_no_anchor_validation");
        let revision=r.revision;
        let last=frame(11,now,0);
        feed(&mut r,&last,now+TTL+1);
        assert!(!r.health(now+TTL+1).ready); assert!(r.revision>revision);
        let mut f=frame(12,now+TTL+2,0); f["head"]["flags"]=json!(3);
        feed(&mut r,&f,now+TTL+2); assert!(!r.health(now+TTL+2).ready);
        feed(&mut r,&frame(13,now+1_000_000_000,0),now+1_000_000_000);
        assert!(!r.health(now+1_000_000_000).ready);
    }
    #[test]
    fn anchors_detect_coherent_relocalization_and_do_not_fallback_after_loss() {
        let mut r=Reference::default(); let now=settle(&mut r,10_000_000_000,1,3); let revision=r.revision;
        assert!(r.health(now).ready);
        let mut f=frame(12,now+200_000_000,3);
        // Real user head movement must not look like room drift.
        f["head"]["quest_stage"]["position"]=json!([2,1.6,3]);
        feed(&mut r,&f,now+200_000_000); assert!(r.health(now+200_000_000).ready); assert_eq!(r.revision,revision);
        f["sequence"]=json!(13); f["sample_monotonic_ns"]=json!(now+400_000_000);
        for m in f["markers"].as_array_mut().unwrap() {m["pose"]["quest_stage"]["position"][0]=json!(0.06);}
        feed(&mut r,&f,now+400_000_000); assert!(!r.health(now+400_000_000).ready); assert!(r.revision>revision);
        feed(&mut r,&frame(14,now+600_000_000,2),now+600_000_000);
        assert_eq!(r.health(now+600_000_000).state,"anchors_unavailable");
    }
    #[test]
    fn session_generation_replay_malformed_and_backward_time_invalidate() {
        for mutation in 0..6 {
            let mut r=Reference::default();let now=settle(&mut r,10_000_000_000,1,3);let revision=r.revision;
            let mut f=frame(12,now+200_000_000,3);
            match mutation {
                0=>f["source_session"]=json!("2"),1=>f["generation"]=json!(2),2=>f["sequence"]=json!(10),
                3=>f["head"]["quest_stage"]["rotation"]=json!([0,0,0,0]),
                4=>f["sample_monotonic_ns"]=json!(now-1), _=>f["markers"][1]["uuid"]=f["markers"][0]["uuid"].clone(),
            }
            feed(&mut r,&f,now+200_000_000);
            assert!(!r.health(now+200_000_000).ready,"mutation {mutation}");assert!(r.revision>revision);
        }
    }
    #[test]
    fn anchor_rotation_and_changed_identity_require_settling() {
        for rotate in [false,true] {
            let mut r=Reference::default();let now=settle(&mut r,10_000_000_000,1,3);
            let mut f=frame(12,now+200_000_000,3);
            if rotate {f["markers"][0]["pose"]["quest_stage"]["rotation"]=json!([0,0.04,0,0.9991997]);}
            else {f["markers"][0]["uuid"]=json!("ffffffffffffffffffffffffffffffff");}
            feed(&mut r,&f,now+200_000_000);assert!(!r.health(now+200_000_000).ready);
        }
    }
    #[test]
    fn calibration_requires_deliberate_reset_after_recovery() {
        let shared=Arc::new(RwLock::new(Reference::default()));
        let mut cal=crate::calibration::Calibration::new();cal.attach_reference(shared.clone());
        assert!(cal.prepare_reference_reset(true).is_err());
        let now=now_ns();settle(&mut shared.write().unwrap(),now-SETTLE,1,3);
        assert!(cal.prepare_reference_reset(true).is_ok());assert!(cal.reference_needs_reset());
        cal.confirm_reference_reset();assert!(!cal.reference_needs_reset());
        cal.check_hmd_reference(false);assert!(cal.reference_needs_reset());
        assert!(cal.prepare_reference_reset(false).is_err());
        cal.confirm_reference_reset();
        shared.write().unwrap().invalidate("test_disconnect");cal.sync_reference();
        assert!(cal.reference_needs_reset());assert!(cal.prepare_reference_reset(true).is_err());
        let start=now_ns()-SETTLE;
        for i in 0..=10 {
            let at=start+i*200_000_000;let mut f=frame(20+i as u64,at,3);f["source_session"]=json!("2");
            feed(&mut shared.write().unwrap(),&f,at);
        }
        assert!(cal.prepare_reference_reset(true).is_ok());assert!(cal.reference_needs_reset());
    }
}
