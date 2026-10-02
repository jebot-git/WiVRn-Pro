// SPDX-License-Identifier: GPL-3.0-only
use glam::{Affine3A, Mat3, Quat, Vec2, Vec3};
use openxr as xr;
use std::{
    os::fd::{AsRawFd, FromRawFd, OwnedFd},
    time::{Duration, Instant},
};
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
            || !j[1].location_flags.contains(
                xr::SpaceLocationFlags::POSITION_TRACKED
                    | xr::SpaceLocationFlags::ORIENTATION_VALID,
            )
        {
            self.reset();
            return None;
        }
        let q = j[1].pose.orientation;
        let rotation = Quat::from_xyzw(q.x, q.y, q.z, q.w);
        if !rotation.is_finite() || !(0.5..1.5).contains(&rotation.length_squared()) {
            self.reset();
            return None;
        }
        let wrist = Affine3A::from_rotation_translation(rotation.normalize(), p(1));
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
            if !thumbs_up {
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
            // Mount above the thumb-side knuckles, independently of the raised
            // thumb. Freeze this frame relative to the wrist until the hand opens.
            let rotation = Quat::from_mat3(&Mat3::from_cols(right, forward, normal))
                * Quat::from_rotation_x(60.0_f32.to_radians());
            let fist_top = (p(7) + p(12)) * 0.5;
            let clearance = (width * 0.25).clamp(0.015, 0.025);
            let frame =
                Affine3A::from_rotation_translation(rotation, fist_top + normal * clearance);
            self.local = Some(wrist.inverse() * frame);
            self.radius = (width * 0.35).clamp(0.025, 0.04);
            self.axes = Vec2::ZERO;
        }
        let frame = wrist * self.local.unwrap();
        let tip = frame.inverse().transform_point3(p(5));
        let raw = Vec2::new(tip.x, tip.y) / self.radius;
        let length = raw.length();
        // A thumbs-up starts beyond the pad centre. Require one centre entry
        // before sending axes; do not calibrate neutral to that extended pose.
        self.armed |= length <= 0.25;
        let target = if !self.armed || length <= 0.12 {
            Vec2::ZERO
        } else {
            raw / length * ((length - 0.12) / 0.88).min(1.0)
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
    fn pad_follows_wrist_and_thumb_axes_clamp_and_open_releases() {
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
    fn fixed_fist_mount_allows_full_forward_back_and_side_travel() {
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
                    (pad.frame.translation - alternate_pad.frame.translation).length() < 0.0001
                );
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
