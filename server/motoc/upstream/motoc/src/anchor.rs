// SPDX-License-Identifier: GPL-3.0-only
//! Experimental room-anchored continuation. An initial Motoc calibration supplies
//! the cross-space relationship; anchors alone cannot discover Lighthouse space.
use std::{collections::BTreeMap, io::{Read, Write}, os::fd::AsRawFd, path::PathBuf, sync::atomic::Ordering, time::Duration};
use anyhow::{Context, ensure};
use libmonado::{self as mnd, DeviceLogic};
use libmotoc::TransformD;
use nalgebra::{Quaternion, UnitQuaternion, Vector3};
use serde::{Deserialize, Serialize};

type Anchors = BTreeMap<String, TransformD>;
#[derive(Deserialize)]
struct Pose { position: [f64;3], rotation: [f64;4] }
impl Pose {
    fn checked(&self) -> anyhow::Result<TransformD> {
        ensure!(self.position.iter().chain(self.rotation.iter()).all(|n| n.is_finite()), "Non-finite pose");
        let [x,y,z,w] = self.rotation;
        let q = Quaternion::new(w,x,y,z);
        ensure!((q.norm_squared()-1.0).abs() < 0.02, "Invalid rotation");
        Ok(TransformD { origin: self.position.into(), basis: UnitQuaternion::new_normalize(q).to_rotation_matrix() })
    }
}
#[derive(Deserialize)]
struct Location { valid: bool, flags: u64, quest_stage: Option<Pose> }
#[derive(Deserialize)]
struct Marker { uuid: String, persisted: bool, pose: Location }
#[derive(Deserialize)]
struct Frame { schema: String, source_session: String, sequence: u64, generation: u64, live: bool, sample_monotonic_ns: i64, markers: Vec<Marker> }
#[derive(Serialize, Deserialize)]
struct Saved { version: u32, source: String, target: String, relative: TransformD, anchors: Anchors }
fn path(profile: &str) -> anyhow::Result<PathBuf> {
    ensure!(!profile.is_empty() && profile.bytes().all(|b| b.is_ascii_alphanumeric() || b"-_".contains(&b)), "Invalid profile name");
    let home = std::env::var_os("XDG_CONFIG_HOME").map(PathBuf::from).or_else(|| std::env::var_os("HOME").map(|p| PathBuf::from(p).join(".config"))).context("Missing config home")?;
    Ok(home.join("motoc").join(format!("{profile}.anchors.json")))
}
fn now() -> i64 {
    let mut ts = libc::timespec { tv_sec: 0, tv_nsec: 0 };
    unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts); }
    ts.tv_sec * 1_000_000_000 + ts.tv_nsec
}
fn checked_frame(bytes: &[u8]) -> anyhow::Result<(Frame, Anchors)> {
    let frame: Frame = serde_json::from_slice(bytes)?;
    ensure!(frame.schema == "wivrn.spatial.v1" && frame.live && frame.sequence > 0 && frame.generation > 0 && frame.source_session.parse::<u64>().is_ok_and(|v| v > 0), "Spatial reference unavailable");
    ensure!((-100_000_000..=750_000_000).contains(&(now()-frame.sample_monotonic_ns)), "Stale spatial reference");
    ensure!(frame.markers.len() <= 12, "Too many anchors");
    let mut anchors = Anchors::new();
    for marker in &frame.markers {
        if marker.persisted && marker.pose.valid && marker.pose.flags & 15 == 15 {
            ensure!(anchors.insert(marker.uuid.clone(), marker.pose.quest_stage.as_ref().context("Missing pose")?.checked()?).is_none(), "Duplicate anchor");
        }
    }
    ensure!(anchors.len() >= 3, "Need three persisted, tracked anchors");
    Ok((frame, anchors))
}
/// All common, spatially distributed markers must agree on yaw and translation.
/// A single orientation estimate cannot independently validate a moving anchor.
fn fit(before: &Anchors, after: &Anchors) -> anyhow::Result<TransformD> {
    let pairs: Vec<_> = before.iter().filter_map(|(id,a)| after.get(id).map(|b| (a,b))).collect();
    ensure!(pairs.len() >= 3, "Room anchors do not match the saved calibration");
    ensure!(pairs.iter().enumerate().any(|(i,(a,_))| pairs.iter().skip(i+1).enumerate().any(|(j,(b,_))| pairs.iter().skip(i+j+2).any(|(c,_)| (b.origin-a.origin).cross(&(c.origin-a.origin)).norm() >= 0.05))), "Anchor layout is degenerate");
    let deltas: Vec<_> = pairs.iter().map(|(a,b)| b.basis * a.basis.inverse()).collect();
    let (s,c) = deltas.iter().fold((0.0,0.0), |(s,c),q| {
        let right = q * Vector3::x(); let yaw = (-right.z).atan2(right.x); (s+yaw.sin(),c+yaw.cos())
    });
    let rotation = UnitQuaternion::from_axis_angle(&Vector3::y_axis(), s.atan2(c)).to_rotation_matrix();
    ensure!(deltas.iter().all(|q| (rotation.inverse()*q).angle() < 1f64.to_radians()), "Anchor rotations disagree or tilt changed");
    let translation = pairs.iter().map(|(a,b)| b.origin-rotation*a.origin).sum::<Vector3<f64>>() / pairs.len() as f64;
    ensure!(pairs.iter().all(|(a,b)| (b.origin-(rotation*a.origin+translation)).norm() < 0.02), "Anchor positions disagree");
    Ok(TransformD { basis: rotation, origin: translation })
}
fn near(a: TransformD, b: TransformD) -> bool {
    (a.origin-b.origin).norm() < 0.01 && (a.basis.inverse()*b.basis).angle() < 0.5f64.to_radians()
}
fn connect() -> anyhow::Result<socket2::Socket> {
    let path = std::env::var_os("WIVRN_SPATIAL_SOCKET").context("Missing WiVRn spatial bridge")?;
    let socket = socket2::Socket::new(socket2::Domain::UNIX, socket2::Type::SEQPACKET, None)?;
    socket.connect(&socket2::SockAddr::unix(path)?)?;
    socket.set_read_timeout(Some(Duration::from_millis(500)))?;
    let mut peer: libc::ucred = unsafe {std::mem::zeroed()};
    let mut len = std::mem::size_of_val(&peer) as libc::socklen_t;
    ensure!(unsafe { libc::getsockopt(socket.as_raw_fd(),libc::SOL_SOCKET,libc::SO_PEERCRED,(&mut peer as *mut libc::ucred).cast(),&mut len) } == 0 && peer.uid == unsafe {libc::getuid()}, "Spatial bridge owner mismatch");
    Ok(socket)
}
fn origins<'a>(monado: &'a mnd::Monado, src: &str, dst: &str) -> anyhow::Result<(mnd::TrackingOrigin<'a>,mnd::TrackingOrigin<'a>)> {
    let mut ids = Vec::new();
    for serial in [src,dst] {
        let mut id = None;
        for device in monado.devices()? {
            if device.serial()? == serial { id = Some(device.get_info_u32(mnd::MndProperty::PropertyTrackingOriginU32)?); break; }
        }
        ids.push(id.context(format!("Device unavailable: {serial}"))?);
    }
    ensure!(ids[0] != ids[1], "Devices share the same tracking origin");
    let all: Vec<_> = monado.tracking_origins()?.into_iter().collect();
    let find = |id| all.iter().find(|o| o.id == id).cloned().context("Tracking origin missing");
    Ok((find(ids[0])?,find(ids[1])?))
}

pub fn run(monado: &mnd::Monado, profile: &str, capture: Option<(&str,&str)>) -> anyhow::Result<()> {
    let path = path(profile)?;
    let mut saved: Option<Saved> = if capture.is_none() { Some(serde_json::from_reader(std::fs::File::open(&path).context("Calibrate anchor mode first")?)?) } else { None };
    if let Some(s) = &saved { ensure!(s.version == 1 && s.anchors.len() >= 3, "Invalid anchor profile"); }
    let (src,dst) = capture.map(|(a,b)|(a.to_owned(),b.to_owned())).unwrap_or_else(|| { let s=saved.as_ref().unwrap(); (s.source.clone(),s.target.clone()) });
    if let (Ok(expected_src),Ok(expected_dst))=(std::env::var("WIVRN_MOTOC_SOURCE"),std::env::var("WIVRN_MOTOC_TARGET")) {
        ensure!(src == expected_src && dst == expected_dst, "Saved anchor profile belongs to different devices; calibrate again");
    }
    let (source,target) = origins(monado,&src,&dst)?;
    let relative = TransformD::from(source.get_offset()?).inverse() * TransformD::from(target.get_offset()?);
    let mut socket = None;
    let mut settled: Option<(TransformD,i64)> = None;
    let mut baseline: Option<Anchors> = None;
    let mut last: Option<(String,u64,u64,i64)> = None;
    let mut last_log = String::new();
    while crate::RUNNING.load(Ordering::Relaxed) {
        if socket.is_none() {
            match connect() { Ok(s) => socket=Some(s), Err(_) => { std::thread::sleep(Duration::from_millis(250)); continue; } }
        }
        let mut bytes = vec![0u8;128*1024+1];
        let count = match socket.as_mut().unwrap().read(&mut bytes) {
            Ok(n) if n>0 && n<bytes.len() => n,
            _ => { socket=None; settled=None; baseline=None; continue; }
        };
        let update = (|| -> anyhow::Result<()> {
            let (frame,anchors) = checked_frame(&bytes[..count])?;
            let stamp = (frame.source_session.clone(),frame.generation,frame.sequence,frame.sample_monotonic_ns);
            if let Some((session,generation,seq,time)) = &last {
                if session == &stamp.0 { ensure!(stamp.2 > *seq && stamp.3 > *time, "Repeated spatial frame"); }
                if session != &stamp.0 || generation != &stamp.1 || stamp.3-time > 750_000_000 { settled=None; baseline=None; }
            }
            last=Some(stamp);
            let reference = if let Some(s)=&saved { &s.anchors } else { baseline.get_or_insert_with(||anchors.clone()) };
            let delta=fit(reference,&anchors)?;
            let at=now();
            match settled {
                Some((previous,since)) if near(previous,delta) => {
                    if at-since < 2_000_000_000 { return Ok(()); }
                }
                _ => { settled=Some((delta,at)); return Ok(()); }
            }
            if saved.is_none() {
                let data=Saved { version:1, source:src.clone(),target:dst.clone(),relative,anchors:anchors.clone() };
                std::fs::create_dir_all(path.parent().unwrap())?;
                let mut tmp=tempfile::NamedTempFile::new_in(path.parent().unwrap())?;
                tmp.write_all(&serde_json::to_vec(&data)?)?; tmp.as_file().sync_all()?; tmp.persist(&path)?;
                saved=Some(data); settled=None;
                log::info!("Room anchor reference saved; tracker may now be detached");
                println!("WIVRN_MOTOC_READY");
                return Ok(());
            }
            let data=saved.as_ref().unwrap();
            // Root_from_LH = Root_from_Quest(now) * Quest(now)_from_Quest(saved)
            //                * Quest(saved)_from_LH. Never modify Quest/STAGE itself.
            let target_pose=TransformD::from(source.get_offset()?) * delta * data.relative;
            ensure!(target_pose.origin.iter().all(|v|v.is_finite()) && target_pose.origin.norm()<100.0, "Implausible anchor correction");
            target.set_offset(target_pose.into())?;
            if last_log != "active" { log::info!("Anchor continuation active (experimental)"); last_log="active".into(); }
            Ok(())
        })();
        if let Err(e)=update {
            settled=None; baseline=None;
            let text=e.to_string();
            if text != last_log { log::warn!("Anchor continuation paused: {text}"); last_log=text; }
        }
    }
    Ok(())
}
#[cfg(test)]
mod tests {
    use super::*;
    fn anchors() -> Anchors {
        [[0.,0.,0.],[1.,0.,0.],[0.,0.,1.],[1.,0.,1.]].into_iter().enumerate().map(|(i,p)|(i.to_string(),TransformD{origin:p.into(),..Default::default()})).collect()
    }
    #[test] fn recovers_yaw_translation_and_rejects_bad_references() {
        let before=anchors(); let expected=TransformD{basis:UnitQuaternion::from_axis_angle(&Vector3::y_axis(),0.7).to_rotation_matrix(),origin:Vector3::new(1.,0.2,-2.)};
        let mut after: Anchors=before.iter().map(|(id,p)|(id.clone(),expected * *p)).collect();
        assert!(near(fit(&before,&after).unwrap(),expected));
        after.get_mut("0").unwrap().origin.x+=0.1; assert!(fit(&before,&after).is_err());
        assert!(fit(&before,&Anchors::new()).is_err());
        let single=before.iter().take(1).map(|(a,b)|(a.clone(),*b)).collect(); assert!(fit(&single,&single).is_err());
    }
    #[test] fn composition_is_correct_with_nonidentity_source_origin() {
        let source=TransformD{basis:UnitQuaternion::from_axis_angle(&Vector3::y_axis(),0.3).to_rotation_matrix(),origin:Vector3::new(2.,0.,1.)};
        let target=TransformD{origin:Vector3::new(-1.,0.,2.),..Default::default()};
        assert!(near(source * (source.inverse()*target),target));
    }
    #[test]
    fn rejects_stale_untracked_duplicate_and_malformed_spatial_samples() {
        let marker=|id: &str,p:[f64;3]| serde_json::json!({"uuid":id,"persisted":true,"pose":{"valid":true,"flags":15,"quest_stage":{"position":p,"rotation":[0,0,0,1]}}});
        let good=serde_json::json!({"schema":"wivrn.spatial.v1","source_session":"123","sequence":1,"generation":1,"live":true,"sample_monotonic_ns":now(),"markers":[marker("a",[0.,0.,0.]),marker("b",[1.,0.,0.]),marker("c",[0.,0.,1.])]});
        assert!(checked_frame(&serde_json::to_vec(&good).unwrap()).is_ok());
        for key in ["stale","untracked","duplicate","rotation"] {
            let mut bad=good.clone();
            match key {
                "stale" => bad["sample_monotonic_ns"]=serde_json::json!(now()-1_000_000_000),
                "untracked" => bad["live"]=false.into(),
                "duplicate" => bad["markers"][1]["uuid"]="a".into(),
                _ => bad["markers"][0]["pose"]["quest_stage"]["rotation"]=serde_json::json!([0,0,0,0]),
            }
            assert!(checked_frame(&serde_json::to_vec(&bad).unwrap()).is_err(),"{key}");
        }
    }
    #[test] fn rejects_profile_path_traversal() { assert!(path("../last").is_err()); }
}
