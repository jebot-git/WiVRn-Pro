// SPDX-License-Identifier: GPL-3.0-only
use glam::{Affine3A, Mat3, Quat, Vec2, Vec3};
use openxr as xr;
use std::{
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    time::{Duration, Instant},
};
pub const DEADZONE: f32 = 0.12;

#[derive(Clone, Copy, Debug)]
pub struct Pad {
    pub frame: Affine3A,
    pub radius: f32,
    pub axes: Vec2,
    pub cursor: Vec2,
    pub armed: bool,
}
#[derive(Default)]
pub struct ThumbPad {
    since: Option<Duration>,
    local: Option<Affine3A>,
    index_mounted: bool,
    axes: Vec2,
    armed: bool,
    radius: f32,
    last: Option<Duration>,
}
impl ThumbPad {
    pub fn reset(&mut self) {
        *self = Self::default();
    }
    pub fn owns_hand(&self) -> bool {
        self.since.is_some() || self.local.is_some()
    }
    pub fn update(
        &mut self,
        now: Duration,
        joints: Option<&xr::HandJointLocations>,
        head: Affine3A,
    ) -> Option<Pad> {
        if !head.is_finite() {
            self.reset();
            return None;
        }
        let Some(j) = joints else {
            self.reset();
            return None;
        };
        let p = |i: usize| {
            Vec3::new(
                j[i].pose.position.x,
                j[i].pose.position.y,
                j[i].pose.position.z,
            )
        };
        let valid = xr::SpaceLocationFlags::POSITION_VALID;
        if j.iter().any(|v| !v.location_flags.contains(valid))
            || (0..26).any(|i| !p(i).is_finite())
            || [7, 8, 12, 13, 17, 18, 22, 23].iter().any(|&i| {
                !j[i]
                    .location_flags
                    .contains(xr::SpaceLocationFlags::POSITION_TRACKED)
            })
        {
            self.reset();
            return None;
        }
        let width = p(7).distance(p(22));
        if !(0.03..=0.13).contains(&width) {
            self.reset();
            return None;
        }
        let extension = [7, 12, 17, 22].map(|b| {
            let length = (b..b + 3).map(|i| p(i).distance(p(i + 1))).sum::<f32>();
            if length > 0.015 {
                p(b).distance(p(b + 3)) / length
            } else {
                1.0
            }
        });
        if extension.iter().filter(|v| **v > 0.82).count() >= 2 {
            self.reset();
            return None;
        }
        let index_threshold = if self.local.is_some() && self.index_mounted {
            0.72
        } else {
            0.82
        };
        let gun_grip = extension[0] > index_threshold
            && extension[1..].iter().filter(|v| **v < 0.65).count() >= 2;
        if self.local.is_some() && gun_grip != self.index_mounted {
            // A different support pose needs a fresh centre and mount.
            self.reset();
        }
        let mount_index = if self.local.is_some() {
            self.index_mounted
        } else {
            gun_grip
        };
        // Release input when the supporting index segment is no longer tracked.
        let support = if mount_index { [9, 10] } else { [8, 9] };
        if support.iter().any(|&i| {
            !j[i]
                .location_flags
                .contains(xr::SpaceLocationFlags::POSITION_TRACKED)
        }) {
            self.reset();
            return None;
        }
        let across = (p(7) - p(22)) / width;
        let (origin, along) = if mount_index {
            (p(10), p(10) - p(9))
        } else {
            let knuckles = (p(7) + p(12) + p(17) + p(22)) * 0.25;
            let curled = (p(8) + p(13) + p(18) + p(23)) * 0.25;
            // Place the large pad on the fleshy, inside surface of the middle
            // segment, toward the index base in the finger's curl plane.
            let segment = p(9) - p(8);
            if segment.length_squared() < 0.005_f32.powi(2) {
                self.reset();
                return None;
            }
            let segment = segment.normalize();
            let middle = (p(8) + p(9)) * 0.5;
            let inward = p(7) - middle;
            let inward = inward - segment * inward.dot(segment);
            if inward.length_squared() < 0.005_f32.powi(2) {
                self.reset();
                return None;
            }
            let radius = (j[8].radius + j[9].radius) * 0.5;
            let clearance = if radius.is_finite() {
                radius.clamp(0.004, 0.012) + 0.002
            } else {
                0.010
            };
            (middle + inward.normalize() * clearance, knuckles - curled)
        };
        let along = along - across * along.dot(across);
        if along.length_squared() < 0.005_f32.powi(2) {
            self.reset();
            return None;
        }
        let along = along.normalize();
        let mount = Affine3A::from_mat3_translation(
            Mat3::from_cols(across, along, across.cross(along)),
            origin,
        );
        if self
            .last
            .is_some_and(|t| now.saturating_sub(t) > Duration::from_millis(150))
        {
            self.reset();
        }
        let dt = self.last.map_or(0.016, |t| {
            now.saturating_sub(t).as_secs_f32().clamp(0.001, 0.05)
        });
        self.last = Some(now);
        if self.local.is_none() {
            let thumb = p(5) - p(2);
            let length = (2..5).map(|i| p(i).distance(p(i + 1))).sum::<f32>();
            let thumbs_up = extension[0] < 0.65
                && extension.iter().filter(|v| **v < 0.65).count() >= 3
                && length > 0.025
                && thumb.length() / length > 0.85
                && thumb
                    .normalize()
                    .dot(head.transform_vector3(Vec3::Y).normalize())
                    > 0.55;
            let elevation = if thumb.length_squared() > 0.0001 {
                thumb
                    .normalize()
                    .dot(head.transform_vector3(Vec3::Y).normalize())
            } else {
                1.0
            };
            // Fully upright is the end of the thumb's reach, not a neutral
            // joystick pose. Wait for it to lower while the index is extended.
            let relaxed_gun = gun_grip && (0.20..=0.70).contains(&elevation);
            if !(if mount_index { relaxed_gun } else { thumbs_up }) {
                self.since = None;
                return None;
            }
            let at = *self.since.get_or_insert(now);
            if now.saturating_sub(at) < Duration::from_millis(150) {
                return None;
            }
            let normal = head.transform_vector3(Vec3::Y).normalize();
            let right = head.transform_vector3(Vec3::X).normalize();
            let forward = normal.cross(right).normalize();
            // Preserve the accepted tilt, but put index pads on the fingertip
            // itself; the larger pad sits inside the curled middle segment.
            let rotation = Quat::from_mat3(&Mat3::from_cols(right, forward, normal))
                * Quat::from_rotation_x(60.0_f32.to_radians());
            let center = origin;
            let frame = Affine3A::from_rotation_translation(rotation, center);
            self.local = Some(mount.inverse() * frame);
            self.index_mounted = mount_index;
            self.radius = if mount_index {
                (width * 0.24).clamp(0.014, 0.020)
            } else {
                (width * 0.35).clamp(0.025, 0.04)
            };
            self.axes = Vec2::ZERO;
        }
        let frame = mount * self.local.unwrap();
        let tip = frame.inverse().transform_point3(p(5));
        let raw = Vec2::new(tip.x, tip.y) / self.radius;
        let length = raw.length();
        // A fingertip-mounted pad stays neutral until the thumb reaches its
        // centre. Preserve the centre gate and radial deadzone.
        self.armed |= length <= 0.25;
        let target = if !self.armed || length <= DEADZONE {
            Vec2::ZERO
        } else {
            raw / length * ((length - DEADZONE) / (1.0 - DEADZONE)).min(1.0)
        };
        self.axes = self.axes.lerp(target, 1.0 - (-dt / 0.03).exp());
        Some(Pad {
            frame,
            radius: self.radius,
            axes: self.axes,
            cursor: raw.clamp_length_max(1.0),
            armed: self.armed,
        })
    }
}

/// Opt-in, two-minute local capture for thumb-pad ergonomics. Coordinates are
/// relative to the wrist in head axes, not absolute room positions.
pub fn diagnostic_sample(
    side: usize,
    now: Duration,
    joints: Option<&xr::HandJointLocations>,
    head: Affine3A,
    pad: Option<Pad>,
) {
    use std::{
        io::Write,
        os::unix::fs::OpenOptionsExt,
        sync::{Mutex, OnceLock},
    };
    static LAST: OnceLock<Mutex<[Duration; 2]>> = OnceLock::new();
    let mut last = LAST
        .get_or_init(|| Mutex::new([Duration::ZERO; 2]))
        .lock()
        .unwrap();
    if side > 1 || now.saturating_sub(last[side]) < Duration::from_millis(100) {
        return;
    }
    last[side] = now;
    let Some(runtime) = std::env::var_os("XDG_RUNTIME_DIR") else {
        return;
    };
    let root = std::path::PathBuf::from(runtime).join("wivrn-pro");
    let enabled = std::fs::metadata(root.join("thumbpad-diagnostics.enabled"))
        .and_then(|m| m.modified())
        .ok()
        .and_then(|t| t.elapsed().ok())
        .is_some_and(|age| age < Duration::from_secs(120));
    if !enabled {
        return;
    }
    let Some(j) = joints else {
        return;
    };
    if !head.is_finite() {
        return;
    }
    let point = |i: usize| {
        Vec3::new(
            j[i].pose.position.x,
            j[i].pose.position.y,
            j[i].pose.position.z,
        )
    };
    let relative: Vec<_> = (0..26)
        .map(|i| {
            head.inverse()
                .transform_vector3(point(i) - point(1))
                .to_array()
        })
        .collect();
    let record = serde_json::json!({
        "time":now.as_secs_f64(), "side":side, "relative_joints":relative,
        "valid":j.iter().all(|v| v.location_flags.contains(xr::SpaceLocationFlags::POSITION_VALID)),
        "pad":pad.map(|p| serde_json::json!({"thumb":p.frame.inverse().transform_point3(point(5)).to_array(),
            "axes":p.axes.to_array(), "radius":p.radius,
            "center":head.inverse().transform_vector3(Vec3::from(p.frame.translation)-point(1)).to_array()})),
    });
    let path = root.join("thumbpad-diagnostics.jsonl");
    if std::fs::metadata(&path).is_ok_and(|m| m.len() >= 8 * 1024 * 1024) {
        return;
    }
    if let Ok(mut file) = std::fs::OpenOptions::new()
        .create(true)
        .append(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
    {
        let _ = writeln!(file, "{record}");
    }
}

pub struct Link {
    fd: Option<OwnedFd>,
    retry: Option<Instant>,
    ack: Option<Instant>,
    pub hand_mask: u8,
    sequence: u64,
}
impl Default for Link {
    fn default() -> Self {
        Self {
            fd: None,
            retry: None,
            ack: None,
            hand_mask: 0,
            sequence: 0,
        }
    }
}
impl Link {
    pub fn native_hands(&self) -> u8 {
        if self
            .ack
            .is_some_and(|t| t.elapsed() < Duration::from_millis(150))
        {
            self.hand_mask
        } else {
            0
        }
    }
    pub fn disconnect(&mut self) {
        self.fd = None;
        self.ack = None;
        self.hand_mask = 0;
    }
    fn connect(&mut self) {
        if self.fd.is_some()
            || self
                .retry
                .is_some_and(|t| t.elapsed() < Duration::from_secs(1))
        {
            return;
        }
        self.retry = Some(Instant::now());
        let Some(runtime) = std::env::var_os("XDG_RUNTIME_DIR") else {
            return;
        };
        let path = std::path::PathBuf::from(runtime).join("wivrn-pro/hand-input.sock");
        use std::os::unix::ffi::OsStrExt;
        let bytes = path.as_os_str().as_bytes();
        unsafe {
            let mut addr: libc::sockaddr_un = std::mem::zeroed();
            if bytes.len() >= addr.sun_path.len() {
                return;
            }
            addr.sun_family = libc::AF_UNIX as _;
            for (a, b) in addr.sun_path.iter_mut().zip(bytes) {
                *a = *b as _;
            }
            let raw = libc::socket(
                libc::AF_UNIX,
                libc::SOCK_SEQPACKET | libc::SOCK_NONBLOCK | libc::SOCK_CLOEXEC,
                0,
            );
            if raw < 0 {
                return;
            }
            let fd = OwnedFd::from_raw_fd(raw);
            if libc::connect(
                raw,
                &addr as *const _ as *const _,
                std::mem::size_of_val(&addr) as _,
            ) != 0
            {
                return;
            }
            let mut peer: libc::ucred = std::mem::zeroed();
            let mut len = std::mem::size_of_val(&peer) as libc::socklen_t;
            if libc::getsockopt(
                raw,
                libc::SOL_SOCKET,
                libc::SO_PEERCRED,
                &mut peer as *mut _ as *mut _,
                &mut len,
            ) != 0
                || peer.uid != libc::getuid()
            {
                return;
            }
            self.fd = Some(fd);
            self.sequence = 0;
            log::info!("WiVRn joystick input channel connected");
        }
    }
    pub fn exchange(&mut self, enabled: bool, pads: [Option<Pad>; 2]) {
        self.connect();
        let Some(fd) = self.fd.as_ref().map(AsRawFd::as_raw_fd) else {
            return;
        };
        unsafe {
            for _ in 0..8 {
                let mut reply = [0u8; 9];
                let n = libc::recv(
                    fd,
                    reply.as_mut_ptr() as _,
                    reply.len(),
                    libc::MSG_DONTWAIT | libc::MSG_TRUNC,
                );
                if n < 0 {
                    if std::io::Error::last_os_error().kind() != std::io::ErrorKind::WouldBlock {
                        self.disconnect();
                    }
                    break;
                }
                if n != 8 || &reply[..4] != b"WVJA" {
                    self.disconnect();
                    return;
                }
                self.hand_mask = reply[4] & 3;
                self.ack = Some(Instant::now());
            }
        }
        if self.fd.is_none() {
            return;
        }
        let mut packet = [0u8; 40];
        packet[..4].copy_from_slice(b"WVJ1");
        let flags = u32::from(enabled)
            | ((u32::from(pads[0].is_some()) | u32::from(pads[1].is_some()) << 1) << 1);
        packet[4..8].copy_from_slice(&flags.to_le_bytes());
        let mut ts: libc::timespec = unsafe { std::mem::zeroed() };
        unsafe { libc::clock_gettime(libc::CLOCK_MONOTONIC, &mut ts) };
        let nanos = ts.tv_sec as u64 * 1_000_000_000 + ts.tv_nsec as u64;
        packet[8..16].copy_from_slice(&nanos.to_le_bytes());
        for h in 0..2 {
            let axes = pads[h].map_or(Vec2::ZERO, |p| p.axes);
            packet[16 + h * 8..20 + h * 8].copy_from_slice(&axes.x.to_le_bytes());
            packet[20 + h * 8..24 + h * 8].copy_from_slice(&axes.y.to_le_bytes());
        }
        self.sequence += 1;
        packet[32..40].copy_from_slice(&self.sequence.to_le_bytes());
        let n = unsafe {
            libc::send(
                fd,
                packet.as_ptr() as _,
                packet.len(),
                libc::MSG_DONTWAIT | libc::MSG_NOSIGNAL,
            )
        };
        if n < 0 && std::io::Error::last_os_error().kind() != std::io::ErrorKind::WouldBlock {
            self.disconnect();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    fn joints() -> xr::HandJointLocations {
        let joint = xr::HandJointLocation {
            location_flags: xr::SpaceLocationFlags::POSITION_VALID
                | xr::SpaceLocationFlags::POSITION_TRACKED
                | xr::SpaceLocationFlags::ORIENTATION_VALID,
            pose: xr::Posef::IDENTITY,
            radius: 0.01,
        };
        let mut j = [joint; 26];
        for (base, x) in [(7, 0.04), (12, 0.01), (17, -0.015), (22, -0.04)] {
            for (k, (y, z)) in [(0.0, 0.0), (-0.02, -0.03), (-0.05, -0.015), (-0.05, 0.01)]
                .into_iter()
                .enumerate()
            {
                j[base + k].pose.position = xr::Vector3f { x, y, z };
            }
        }
        for i in 2..6 {
            j[i].pose.position = xr::Vector3f {
                x: 0.05,
                y: (i - 2) as f32 * 0.02,
                z: 0.0,
            };
        }
        j
    }
    fn activate(p: &mut ThumbPad, j: &xr::HandJointLocations) -> Pad {
        assert!(
            p.update(Duration::ZERO, Some(j), Affine3A::IDENTITY)
                .is_none()
        );
        assert!(p.owns_hand());
        assert!(
            p.update(Duration::from_millis(100), Some(j), Affine3A::IDENTITY)
                .is_none()
        );
        p.update(Duration::from_millis(150), Some(j), Affine3A::IDENTITY)
            .unwrap()
    }
    #[test]
    fn pad_follows_fist_and_thumb_axes_clamp_and_open_releases() {
        for mirror in [1.0, -1.0] {
            let mut j = joints();
            for v in &mut j {
                v.pose.position.x *= mirror;
            }
            let mut p = ThumbPad::default();
            let spawned = activate(&mut p, &j);
            assert_eq!(spawned.axes, Vec2::ZERO);
            assert!(!spawned.armed);
            let neutral = spawned.frame.transform_point3(Vec3::Z * 0.012);
            j[5].pose.position = xr::Vector3f {
                x: neutral.x,
                y: neutral.y,
                z: neutral.z,
            };
            let original = p
                .update(Duration::from_millis(155), Some(&j), Affine3A::IDENTITY)
                .unwrap();
            assert!(original.armed && original.axes.length() < 0.001);
            for v in &mut j {
                v.pose.position.x += 0.2;
                v.pose.position.y += 0.1;
            }
            let carried = p
                .update(Duration::from_millis(160), Some(&j), Affine3A::IDENTITY)
                .unwrap();
            assert!(carried.axes.length() < 0.001);
            assert!(
                (carried.frame.translation
                    - original.frame.translation
                    - glam::Vec3A::new(0.2, 0.1, 0.0))
                .length()
                    < 0.001
            );
            j[5].pose.position.x += 0.1;
            let moved = p
                .update(Duration::from_millis(210), Some(&j), Affine3A::IDENTITY)
                .unwrap();
            assert!(moved.axes.x > 0.7 && moved.axes.length() <= 1.0);
            for b in [7, 12] {
                for k in 0..4 {
                    j[b + k].pose.position.y = k as f32 * 0.03;
                    j[b + k].pose.position.z = 0.0;
                }
            }
            assert!(
                p.update(Duration::from_millis(220), Some(&j), Affine3A::IDENTITY)
                    .is_none()
            );
            assert!(!p.owns_hand());
        }
    }
    #[test]
    fn inside_middle_mount_ignores_thumb_height_and_allows_full_axis_travel() {
        for mirror in [1.0, -1.0] {
            for direction in [Vec2::X, Vec2::NEG_X, Vec2::Y, Vec2::NEG_Y] {
                let mut j = joints();
                for v in &mut j {
                    v.pose.position.x *= mirror;
                }
                let mut alternate = j;
                for v in &mut alternate[2..6] {
                    v.pose.position.y += 0.025;
                }
                let mut other = ThumbPad::default();
                let alternate_pad = activate(&mut other, &alternate);
                let mut p = ThumbPad::default();
                let pad = activate(&mut p, &j);
                assert!(
                    (alternate_pad.frame.translation - pad.frame.translation).length() < 0.0001
                );
                // The middle segment's inside normal points toward +Y/+Z,
                // identically for either hand (12 mm surface clearance).
                let expected = Vec3::new(0.04 * mirror, -0.02963344, -0.01176687);
                assert!((Vec3::from(pad.frame.translation) - expected).length() < 0.0001);
                let neutral = pad.frame.transform_point3(Vec3::Z * 0.012);
                j[5].pose.position = xr::Vector3f {
                    x: neutral.x,
                    y: neutral.y,
                    z: neutral.z,
                };
                assert!(
                    p.update(Duration::from_millis(160), Some(&j), Affine3A::IDENTITY)
                        .unwrap()
                        .armed
                );
                let target = pad.frame.transform_point3(Vec3::new(
                    direction.x * pad.radius,
                    direction.y * pad.radius,
                    0.012,
                ));
                j[5].pose.position = xr::Vector3f {
                    x: target.x,
                    y: target.y,
                    z: target.z,
                };
                let mut moved = pad;
                for time in [190, 220, 250, 280, 310] {
                    moved = p
                        .update(Duration::from_millis(time), Some(&j), Affine3A::IDENTITY)
                        .unwrap();
                    assert!((moved.frame.translation - pad.frame.translation).length() < 0.0001);
                }
                assert!(moved.axes.dot(direction) > 0.99);
                assert!((moved.cursor - direction).length() < 0.001);
            }
        }
    }
    #[test]
    fn large_pad_follows_curled_middle_segment_and_releases_on_tracking_loss() {
        let mut j = joints();
        let mut p = ThumbPad::default();
        let pad = activate(&mut p, &j);
        for i in [8, 9] {
            j[i].pose.position.y += 0.005;
        }
        let flexed = p
            .update(Duration::from_millis(160), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert!(!p.index_mounted);
        assert!(
            (Vec3::from(flexed.frame.translation - pad.frame.translation) - Vec3::Y * 0.005)
                .length()
                < 0.0001
        );
        j[9].location_flags = xr::SpaceLocationFlags::POSITION_VALID;
        assert!(
            p.update(Duration::from_millis(180), Some(&j), Affine3A::IDENTITY)
                .is_none()
        );
        assert!(!p.owns_hand());
    }
    #[test]
    fn fist_mount_ignores_wrist_estimate_and_carries_rotation() {
        let mut j = joints();
        let mut p = ThumbPad::default();
        let pad = activate(&mut p, &j);
        // A changed wrist estimate must not chase the thumb or move the pad.
        j[1].pose.position.y += 0.04;
        j[1].pose.orientation = xr::Quaternionf {
            x: 0.5,
            y: 0.0,
            z: 0.0,
            w: 0.8660254,
        };
        let stable = p
            .update(Duration::from_millis(160), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert!((stable.frame.translation - pad.frame.translation).length() < 0.0001);
        assert!(stable.axes.length() < 0.0001);
        let motion = Affine3A::from_rotation_translation(
            Quat::from_rotation_y(0.7),
            Vec3::new(0.2, 0.3, -0.1),
        );
        for joint in &mut j {
            let v = joint.pose.position;
            let v = motion.transform_point3(Vec3::new(v.x, v.y, v.z));
            joint.pose.position = xr::Vector3f {
                x: v.x,
                y: v.y,
                z: v.z,
            };
        }
        let carried = p
            .update(Duration::from_millis(180), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert!((carried.frame.translation - (motion * pad.frame).translation).length() < 0.0001);
        assert!(
            (carried.frame.transform_vector3(Vec3::Y)
                - (motion * pad.frame).transform_vector3(Vec3::Y))
            .length()
                < 0.0001
        );
        assert!(carried.axes.length() < 0.0001);
    }
    #[test]
    fn index_mount_accepts_relaxed_thumb_and_excludes_thumb_and_wrist_motion() {
        let mut j = joints();
        for k in 0..4 {
            j[7 + k].pose.position = xr::Vector3f {
                x: 0.04,
                y: 0.0,
                z: -(k as f32) * 0.025,
            };
        }
        let mut p = ThumbPad::default();
        assert!(
            p.update(Duration::ZERO, Some(&j), Affine3A::IDENTITY)
                .is_none()
        );
        assert!(
            p.update(Duration::from_millis(180), Some(&j), Affine3A::IDENTITY)
                .is_none()
        );
        // Lower the thumb, preserving an extended index and three curled fingers.
        for k in 0..4 {
            j[2 + k].pose.position = xr::Vector3f {
                x: 0.05,
                y: k as f32 * 0.01,
                z: -(k as f32) * 0.018,
            };
        }
        p.reset();
        let pad = activate(&mut p, &j);
        assert!(p.index_mounted && !pad.armed);
        assert!(pad.axes.length() < 0.001 && pad.radius <= 0.02);
        let index_tip = Vec3::new(
            j[10].pose.position.x,
            j[10].pose.position.y,
            j[10].pose.position.z,
        );
        assert!((Vec3::from(pad.frame.translation) - index_tip).length() < 0.0001);
        let neutral = pad.frame.transform_point3(Vec3::Z * 0.012);
        j[5].pose.position = xr::Vector3f {
            x: neutral.x,
            y: neutral.y,
            z: neutral.z,
        };
        let armed = p
            .update(Duration::from_millis(160), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert!(armed.armed && armed.axes.length() < 0.001);
        j[1].pose.position.y += 0.05;
        let target = pad
            .frame
            .transform_point3(Vec3::new(0.0, pad.radius, 0.012));
        j[5].pose.position = xr::Vector3f {
            x: target.x,
            y: target.y,
            z: target.z,
        };
        let moved = p
            .update(Duration::from_millis(200), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert!((moved.frame.translation - pad.frame.translation).length() < 0.0001);
        assert!(moved.axes.y > 0.7);
        // Slight index flex should retain the pad without another activation.
        j[9].pose.position = xr::Vector3f {
            x: 0.04,
            y: -0.024,
            z: -0.023,
        };
        j[10].pose.position = xr::Vector3f {
            x: 0.04,
            y: -0.035,
            z: -0.04,
        };
        let flexed = p
            .update(Duration::from_millis(230), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert!(p.index_mounted);
        let tip = Vec3::new(
            j[10].pose.position.x,
            j[10].pose.position.y,
            j[10].pose.position.z,
        );
        assert!((Vec3::from(flexed.frame.translation) - tip).length() < 0.0001);
        assert!(
            (flexed.frame.transform_vector3(Vec3::Y) - pad.frame.transform_vector3(Vec3::Y))
                .length()
                > 0.01
        );
        j[10].location_flags = xr::SpaceLocationFlags::POSITION_VALID;
        assert!(
            p.update(Duration::from_millis(250), Some(&j), Affine3A::IDENTITY)
                .is_none()
        );
    }
    #[test]
    fn replay_opt_in_hand_diagnostics() {
        let Ok(path) = std::env::var("WIVRN_THUMBPAD_TRACE") else {
            return;
        };
        let text = std::fs::read_to_string(path).unwrap();
        let mut pads: [ThumbPad; 2] = Default::default();
        let mut counts = [0; 2];
        let mut ranges = [[Vec2::ONE, Vec2::NEG_ONE]; 2];
        for line in text.lines() {
            let row: serde_json::Value = serde_json::from_str(line).unwrap();
            let side = row["side"].as_u64().unwrap() as usize;
            if row["valid"] != true {
                continue;
            }
            let mut j = joints();
            for (i, xyz) in row["relative_joints"]
                .as_array()
                .unwrap()
                .iter()
                .enumerate()
            {
                j[i].pose.position = xr::Vector3f {
                    x: xyz[0].as_f64().unwrap() as f32,
                    y: xyz[1].as_f64().unwrap() as f32,
                    z: xyz[2].as_f64().unwrap() as f32,
                };
            }
            if let Some(pad) = pads[side].update(
                Duration::from_secs_f64(row["time"].as_f64().unwrap()),
                Some(&j),
                Affine3A::IDENTITY,
            ) {
                if pads[side].index_mounted {
                    counts[side] += 1;
                    let tip = j[10].pose.position;
                    assert!(
                        (Vec3::from(pad.frame.translation) - Vec3::new(tip.x, tip.y, tip.z))
                            .length()
                            < 0.0001
                    );
                    assert!(pad.axes.is_finite() && pad.axes.length() <= 1.0001);
                    if !pad.armed {
                        assert!(pad.axes.length() < 0.0001);
                    }
                    ranges[side][0] = ranges[side][0].min(pad.axes);
                    ranges[side][1] = ranges[side][1].max(pad.axes);
                }
            }
        }
        eprintln!("Index-pad trace replay samples {counts:?}, axis ranges {ranges:?}");
        assert!(
            counts.iter().all(|n| *n > 20),
            "trace must include both gun poses"
        );
        // This recording predates the fingertip placement. It validates the
        // mount and neutral gate; it cannot establish reach of the new surface.
    }
    #[test]
    fn loss_invalid_pose_and_stream_gap_clear_pad() {
        let j = joints();
        let mut p = ThumbPad::default();
        activate(&mut p, &j);
        assert!(
            p.update(Duration::from_millis(160), None, Affine3A::IDENTITY)
                .is_none()
        );
        assert!(!p.owns_hand());
        activate(&mut p, &j);
        let mut bad = j;
        bad[5].pose.position.x = f32::NAN;
        assert!(
            p.update(Duration::from_millis(160), Some(&bad), Affine3A::IDENTITY)
                .is_none()
        );
        assert!(!p.owns_hand());
        activate(&mut p, &j);
        assert!(
            p.update(Duration::from_millis(400), Some(&j), Affine3A::IDENTITY)
                .is_none()
        );
    }
}
