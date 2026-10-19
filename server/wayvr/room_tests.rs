// SPDX-License-Identifier: GPL-3.0-only
use super::*;
use serde_json::json;

fn frame(sequence: u64, at: i64) -> Frame {
    let pose = json!({"position":[1.0,1.0,-2.0],"rotation":[0,0,0,1]});
    let location = json!({"valid":true,"flags":15,"quest_stage":pose,"pc_stage":pose});
    serde_json::from_value(json!({
        "schema":"wivrn.spatial.v1", "source_session":"123", "generation":1,
        "sequence":sequence, "sample_monotonic_ns":at, "live":true,
        "head":location,
        "markers":[{"uuid":"1234567890abcdef1234567890abcdef", "persisted":true,"pose":location}]
    }))
    .unwrap()
}
fn settle(r: &mut Reference, start: i64) -> i64 {
    let first = r.sequence + 1;
    for i in 0..=10 {
        let t = start + i * 200_000_000;
        r.ingest(1, &frame(first + i as u64, t), t);
    }
    start + SETTLE
}
fn close(a: Affine3A, b: Affine3A) {
    for (a, b) in a.to_cols_array().into_iter().zip(b.to_cols_array()) {
        assert!((a - b).abs() < 0.0001, "{a} != {b}");
    }
}

#[test]
fn room_pose_roundtrip_and_persistence_preserve_scale() {
    let mut r = Reference::default();
    let now = settle(&mut r, 10_000_000_000);
    let window = Affine3A::from_scale_rotation_translation(
        Vec3::splat(0.7),
        Quat::from_rotation_y(0.9),
        Vec3::new(2.0, 1.4, -1.0),
    );
    let binding = r.bind(window, now).unwrap();
    let saved = serde_json::to_string(&binding).unwrap();
    let restored: RoomBinding = serde_json::from_str(&saved).unwrap();
    close(
        r.anchor(&restored.uuid, now).unwrap() * restored.relative,
        window,
    );
}

#[test]
fn restored_window_follows_anchor_after_recenter_and_reconnect() {
    let mut r = Reference::default();
    let now = settle(&mut r, 10_000_000_000);
    let window = Affine3A::from_translation(Vec3::new(2.0, 1.5, -3.0));
    let binding = r.bind(window, now).unwrap();
    let revision = r.revision;
    let rebase =
        Affine3A::from_rotation_translation(Quat::from_rotation_y(0.6), Vec3::new(3.0, 0.3, 2.0));
    for i in 0..=10 {
        let t = now + 200_000_000 + i * 200_000_000;
        let mut f = frame(i as u64 + 1, t);
        f.generation = 2;
        let p = f.markers[0]
            .pose
            .quest_stage
            .as_ref()
            .unwrap()
            .transform()
            .unwrap();
        let q = rebase * p;
        let pose = Pose {
            position: q.translation.into(),
            rotation: Quat::from_affine3(&q).to_array(),
        };
        f.markers[0].pose.quest_stage = Some(pose.clone());
        f.markers[0].pose.pc_stage = Some(pose);
        r.ingest(2, &f, t);
        if i < 10 {
            assert!(r.anchor(&binding.uuid, t).is_none());
        }
    }
    assert!(r.revision > revision);
    close(
        r.anchor(&binding.uuid, now + 200_000_000 + SETTLE).unwrap() * binding.relative,
        rebase * window,
    );
}

#[test]
fn repeated_heartbeat_cannot_refresh_or_settle_a_pose() {
    let mut r = Reference::default();
    let f = frame(1, 10_000_000_000);
    for i in 0..20 {
        r.ingest(1, &f, 10_000_000_000 + i * 200_000_000);
    }
    assert!(r.bind(Affine3A::IDENTITY, 14_000_000_000).is_none());
    let now = settle(&mut r, 20_000_000_000);
    assert!(r.bind(Affine3A::IDENTITY, now).is_some());
    assert!(r.bind(Affine3A::IDENTITY, now + TTL + 1).is_none());
}

#[test]
fn invalid_untracked_duplicate_replay_future_and_missing_samples_fail_closed() {
    for kind in 0..11 {
        let mut r = Reference::default();
        let now = settle(&mut r, 10_000_000_000);
        let mut f = frame(12, now + 200_000_000);
        match kind {
            0 => f.live = false,
            1 => f.head.flags = 3,
            2 => f.markers[0].pose.flags = 3,
            3 => f.markers[0].pose.pc_stage.as_mut().unwrap().rotation = [0.0; 4],
            4 => f.markers.push(f.markers[0].clone()),
            5 => f.sequence = 9,
            6 => f.sample_monotonic_ns = now - 1,
            7 => f.sample_monotonic_ns = now + 1_000_000_000,
            8 => f.markers.clear(),
            9 => f.markers[0].persisted = false,
            _ => f.markers[0].pose.pc_stage.as_mut().unwrap().position[0] = f32::NAN,
        }
        r.ingest(1, &f, now + 200_000_000);
        assert!(
            r.bind(Affine3A::IDENTITY, now + 200_000_000).is_none(),
            "kind {kind}"
        );
    }
}

#[test]
fn pc_offset_changes_do_not_reset_raw_anchor_stability() {
    let mut r = Reference::default();
    let now = settle(&mut r, 10_000_000_000);
    let mut f = frame(12, now + 200_000_000);
    f.markers[0].pose.pc_stage.as_mut().unwrap().position[1] = 1.4;
    r.ingest(1, &f, now + 200_000_000);
    assert!(r.bind(Affine3A::IDENTITY, now + 200_000_000).is_some());
    r.not_before = now + 200_000_000;
    assert!(r.bind(Affine3A::IDENTITY, now + 200_000_000).is_none());
    f.sequence += 1;
    f.sample_monotonic_ns += 200_000_000;
    r.ingest(1, &f, f.sample_monotonic_ns);
    assert!(r.bind(Affine3A::IDENTITY, f.sample_monotonic_ns).is_some());
}

#[test]
fn floor_reconstruction_corrects_height_without_accumulating() {
    let mut r = Reference::default();
    let now = settle(&mut r, 10_000_000_000);
    let binding = r
        .bind(Affine3A::from_translation(Vec3::new(1.0, 0.0, -2.0)), now)
        .unwrap();
    let mut f = frame(12, now + 200_000_000);
    f.markers[0].pose.pc_stage.as_mut().unwrap().position[1] += 0.4;
    r.ingest(1, &f, f.sample_monotonic_ns);
    let world = r.anchor(&binding.uuid, f.sample_monotonic_ns).unwrap() * binding.relative;
    let height = floor_height(world).unwrap();
    assert!((height - 0.4).abs() < 0.0001);
    // Monado offset is root_from_stage. Moving its origin to this floor makes
    // the same physical floor's new STAGE height zero, even with a yaw offset.
    let before =
        Affine3A::from_rotation_translation(Quat::from_rotation_y(0.5), Vec3::new(1.0, 0.2, 3.0));
    let after = floor_offset(before, world).unwrap();
    let corrected = after.inverse() * before * world;
    assert!(floor_height(corrected).unwrap().abs() < 0.0001);
    close(floor_offset(after, corrected).unwrap(), after);
    assert!(floor_offset(before, Affine3A::from_translation(Vec3::Y * 3.0)).is_none());
    assert!(floor_height(Affine3A::from_rotation_x(0.2)).is_none());
    assert!(floor_height(Affine3A::from_scale(Vec3::ZERO)).is_none());
}

#[test]
fn anchor_motion_and_tracking_gap_require_settling_again() {
    for gap in [false, true] {
        let mut r = Reference::default();
        let now = settle(&mut r, 10_000_000_000);
        let mut f = frame(12, now + if gap { TTL + 1 } else { 200_000_000 });
        if !gap {
            f.markers[0].pose.quest_stage.as_mut().unwrap().position[0] += 0.04;
        }
        r.ingest(1, &f, f.sample_monotonic_ns);
        assert!(r.bind(Affine3A::IDENTITY, f.sample_monotonic_ns).is_none());
    }
}

#[test]
fn seqpacket_reader_consumes_real_bridge_records_and_disconnects() {
    let path = std::env::temp_dir().join(format!("wayvr-room-test-{}.sock", std::process::id()));
    let fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC, 0) };
    assert!(fd >= 0);
    let listener = unsafe { OwnedFd::from_raw_fd(fd) };
    let mut addr: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    addr.sun_family = libc::AF_UNIX as _;
    use std::os::unix::ffi::OsStrExt;
    for (a, b) in addr.sun_path.iter_mut().zip(path.as_os_str().as_bytes()) {
        *a = *b as _;
    }
    assert_eq!(
        unsafe {
            libc::bind(
                fd,
                (&addr as *const libc::sockaddr_un).cast(),
                std::mem::size_of_val(&addr) as _,
            )
        },
        0
    );
    assert_eq!(unsafe { libc::listen(fd, 1) }, 0);
    let client = connect(&path).unwrap();
    let peer = unsafe {
        libc::accept4(
            fd,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            libc::SOCK_CLOEXEC,
        )
    };
    assert!(peer >= 0);
    let peer = unsafe { OwnedFd::from_raw_fd(peer) };
    let message = b"{\"live\":false}";
    assert_eq!(
        unsafe {
            libc::send(
                peer.as_raw_fd(),
                message.as_ptr().cast(),
                message.len(),
                libc::MSG_NOSIGNAL,
            )
        },
        message.len() as isize
    );
    let mut data = [0u8; 128];
    assert_eq!(
        unsafe { libc::recv(client.as_raw_fd(), data.as_mut_ptr().cast(), data.len(), 0) },
        message.len() as isize
    );
    assert_eq!(&data[..message.len()], message);
    drop(peer);
    assert_eq!(
        unsafe { libc::recv(client.as_raw_fd(), data.as_mut_ptr().cast(), data.len(), 0) },
        0
    );
    drop(listener);
    std::fs::remove_file(path).unwrap();
}

#[test]
fn background_feed_restores_live_poses_and_clears_on_disconnect() {
    let path = std::env::temp_dir().join(format!("wayvr-room-feed-{}.sock", std::process::id()));
    let fd = unsafe { libc::socket(libc::AF_UNIX, libc::SOCK_SEQPACKET | libc::SOCK_CLOEXEC, 0) };
    assert!(fd >= 0);
    let listener = unsafe { OwnedFd::from_raw_fd(fd) };
    let mut addr: libc::sockaddr_un = unsafe { std::mem::zeroed() };
    addr.sun_family = libc::AF_UNIX as _;
    use std::os::unix::ffi::OsStrExt;
    for (a, b) in addr.sun_path.iter_mut().zip(path.as_os_str().as_bytes()) {
        *a = *b as _;
    }
    assert_eq!(
        unsafe {
            libc::bind(
                fd,
                (&addr as *const libc::sockaddr_un).cast(),
                std::mem::size_of_val(&addr) as _,
            )
        },
        0
    );
    assert_eq!(unsafe { libc::listen(fd, 1) }, 0);
    let mut feed = Feed::default();
    feed.start_at(path.clone());
    let peer = unsafe {
        libc::accept4(
            fd,
            std::ptr::null_mut(),
            std::ptr::null_mut(),
            libc::SOCK_CLOEXEC,
        )
    };
    assert!(peer >= 0);
    let peer = unsafe { OwnedFd::from_raw_fd(peer) };
    for i in 1..=12 {
        let pose = json!({"position":[1,1,-2], "rotation":[0,0,0,1]});
        let location = json!({"valid":true, "flags":15, "quest_stage":pose, "pc_stage":pose});
        let bytes = serde_json::to_vec(&json!({
            "schema":"wivrn.spatial.v1", "source_session":"123", "generation":1,
            "sequence":i, "sample_monotonic_ns":now_ns(), "live":true, "head":location,
            "markers":[{"uuid":"1234567890abcdef1234567890abcdef", "persisted":true,"pose":location}]
        })).unwrap();
        assert_eq!(
            unsafe {
                libc::send(
                    peer.as_raw_fd(),
                    bytes.as_ptr().cast(),
                    bytes.len(),
                    libc::MSG_NOSIGNAL,
                )
            },
            bytes.len() as isize
        );
        std::thread::sleep(Duration::from_millis(210));
        feed.tick();
    }
    let saved = feed
        .bind(Affine3A::IDENTITY)
        .expect("live room marker should settle");
    assert!(feed.locate(&saved).is_some());
    // Do not use a cached future-predicted sample after a PC stage write.
    feed.stage_changed();
    assert!(feed.locate(&saved).is_none());
    drop(peer);
    for _ in 0..50 {
        std::thread::sleep(Duration::from_millis(10));
        feed.tick();
        if feed.mailbox.lock().unwrap().frame.is_none() {
            break;
        }
    }
    assert!(feed.mailbox.lock().unwrap().frame.is_none());
    assert!(feed.locate(&saved).is_none());
    drop(feed);
    drop(listener);
    std::fs::remove_file(path).unwrap();
}
