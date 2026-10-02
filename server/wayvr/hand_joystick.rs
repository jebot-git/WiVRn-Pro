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
    /// Some(pressed) is a visual-only stick button, never an analogue pad.
    pub button: Option<bool>,
}
#[derive(Default)]
pub struct ThumbPad {
    since: Option<Duration>,
    local: Option<Affine3A>,
    click_since: Option<Duration>,
    click_frame: Option<Affine3A>,
    clicked: bool,
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
        self.since.is_some() || self.local.is_some() || self.click_frame.is_some()
    }
    pub fn clicked(&self) -> bool {
        self.clicked
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
        let index_threshold = if self.click_frame.is_some() {
            0.72
        } else {
            0.82
        };
        let thumb_length = (2..5).map(|i| p(i).distance(p(i + 1))).sum::<f32>();
        let thumb_extended = thumb_length > 0.025
            && p(5).distance(p(2)) / thumb_length
                > if self.click_since.is_some() {
                    0.65
                } else {
                    0.75
                };
        let pointing = extension[0] > index_threshold
            && extension[1..].iter().filter(|v| **v < 0.65).count() >= 2;
        if pointing {
            if [4, 5, 9, 10].iter().any(|&i| {
                !j[i]
                    .location_flags
                    .contains(xr::SpaceLocationFlags::POSITION_TRACKED)
            }) {
                self.reset();
                return None;
            }
            // The circle is a button cue only. Extending the thumb debounces
            // the click; relaxing it releases to the translucent pointing cue.
            self.local = None;
            self.since = None;
            self.armed = false;
            self.axes = Vec2::ZERO;
            if thumb_extended {
                let at = *self.click_since.get_or_insert(now);
                self.clicked = now.saturating_sub(at) >= Duration::from_millis(150);
            } else {
                self.click_since = None;
                self.clicked = false;
            }
            let across = (p(7) - p(22)) / width;
            let along = p(10) - p(9);
            let along = along - across * along.dot(across);
            if along.length_squared() < 0.005_f32.powi(2) {
                self.reset();
                return None;
            }
            let along = along.normalize();
            let mount = Affine3A::from_mat3_translation(
                Mat3::from_cols(across, along, across.cross(along)),
                p(10),
            );
            if self.click_frame.is_none() {
                let view_right = head.transform_vector3(Vec3::X);
                let right = Vec3::new(view_right.x, 0.0, view_right.z).normalize_or_zero();
                if right.length_squared() < 0.5 {
                    self.reset();
                    return None;
                }
                let rotation =
                    Quat::from_mat3(&Mat3::from_cols(right, Vec3::Y.cross(right), Vec3::Y))
                        * Quat::from_rotation_x(60.0_f32.to_radians());
                self.click_frame =
                    Some(mount.inverse() * Affine3A::from_rotation_translation(rotation, p(10)));
            }
            return Some(Pad {
                frame: mount * self.click_frame.unwrap(),
                radius: (width * 0.24).clamp(0.014, 0.020),
                axes: Vec2::ZERO,
                cursor: Vec2::ZERO,
                armed: false,
                button: Some(self.clicked),
            });
        }
        self.click_frame = None;
        self.click_since = None;
        self.clicked = false;
        let tracked = |i: usize| {
            j[i].location_flags
                .contains(xr::SpaceLocationFlags::POSITION_TRACKED)
        };
        // MCP/metacarpal positions define a rigid palm frame. Curled finger
        // joints only set the large pad's initial offset, never its live mount.
        let support = [11, 16];
        if support.iter().any(|&i| !tracked(i)) || (self.local.is_none() && !tracked(9)) {
            self.reset();
            return None;
        }
        let across = (p(7) - p(22)) / width;
        let origin = (p(7) + p(12) + p(17) + p(22)) * 0.25;
        let along = (p(12) + p(17) - p(11) - p(16)) * 0.5;
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
            self.radius = (width * 0.35).clamp(0.025, 0.04);
            // Align both pads to view yaw on activation. Head roll/pitch must
            // not skew the axes; thereafter the saved frame rotates with the hand.
            let normal = Vec3::Y;
            let view_right = head.transform_vector3(Vec3::X);
            let right = Vec3::new(view_right.x, 0.0, view_right.z).normalize_or_zero();
            if right.length_squared() < 0.5 {
                self.reset();
                return None;
            }
            let forward = normal.cross(right).normalize();
            let rotation = Quat::from_mat3(&Mat3::from_cols(right, forward, normal))
                * Quat::from_rotation_x(60.0_f32.to_radians());
            let center = {
                // Reference photos: lower rim just above the index, toward the
                // thumb side of the fist. Account for the pad's projected extent
                // so the whole surface clears the finger at the accepted tilt.
                let radius = (j[8].radius + j[9].radius) * 0.5;
                let finger_radius = if radius.is_finite() {
                    radius.clamp(0.004, 0.012)
                } else {
                    0.008
                };
                let pad_x = rotation * Vec3::X;
                let pad_y = rotation * Vec3::Y;
                let extent = self.radius * Vec2::new(pad_x.dot(across), pad_y.dot(across)).length();
                (p(8) + p(9)) * 0.5 + across * (finger_radius + 0.002 + extent)
            };
            let frame = Affine3A::from_rotation_translation(rotation, center);
            self.local = Some(mount.inverse() * frame);
            self.axes = Vec2::ZERO;
        }
        let frame = mount * self.local.unwrap();
        let tip = frame.inverse().transform_point3(p(5));
        let raw = Vec2::new(tip.x, tip.y) / self.radius;
        let length = raw.length();
        // The pad stays neutral until the thumb reaches its
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
            button: None,
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

// WVJ1 flags: enabled, left/right pad active, left/right stick click.
fn command_flags(enabled: bool, active: [bool; 2], clicks: [bool; 2]) -> u32 {
    if !enabled {
        return 0;
    }
    1 | (u32::from(active[0]) << 1)
        | (u32::from(active[1]) << 2)
        | (u32::from(clicks[0]) << 3)
        | (u32::from(clicks[1]) << 4)
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
    pub fn exchange(&mut self, enabled: bool, pads: [Option<Pad>; 2], clicks: [bool; 2]) {
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
        let flags = command_flags(
            enabled,
            pads.map(|p| p.is_some_and(|p| p.button.is_none())),
            clicks,
        );
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
        for (i, x) in [(11, 0.01), (16, -0.015)] {
            j[i].pose.position = xr::Vector3f { x, y: 0.0, z: 0.05 };
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
    fn raised_mount_ignores_thumb_height_and_allows_full_axis_travel() {
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
                // Fixture's radial (thumbward) axis is +/-X. The rim clears
                // the index by its 10 mm radius plus a 2 mm gap.
                let expected = Vec3::new((0.04 + 0.012 + pad.radius) * mirror, -0.035, -0.0225);
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
    fn large_pad_ignores_finger_flex_and_releases_on_palm_tracking_loss() {
        let mut j = joints();
        let mut p = ThumbPad::default();
        let pad = activate(&mut p, &j);
        for base in [7, 12, 17, 22] {
            for i in base + 1..=base + 3 {
                j[i].pose.position.y += 0.005;
            }
        }
        let flexed = p
            .update(Duration::from_millis(160), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert!(!p.clicked());
        assert!((flexed.frame.translation - pad.frame.translation).length() < 0.0001);
        assert!((flexed.frame.matrix3 - pad.frame.matrix3).abs_diff_eq(glam::Mat3A::ZERO, 0.0001));
        j[11].location_flags = xr::SpaceLocationFlags::POSITION_VALID;
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
    fn pad_axes_start_level_and_do_not_follow_head_motion() {
        let j = joints();
        let mut level = ThumbPad::default();
        let expected = activate(&mut level, &j);
        let tilted_head = Affine3A::from_rotation_translation(
            Quat::from_rotation_z(0.35),
            Vec3::new(0.0, 0.2, 0.0),
        );
        let mut tilted = ThumbPad::default();
        for ms in [0, 100] {
            assert!(
                tilted
                    .update(Duration::from_millis(ms), Some(&j), tilted_head)
                    .is_none()
            );
        }
        let pad = tilted
            .update(Duration::from_millis(150), Some(&j), tilted_head)
            .unwrap();
        assert!(pad.frame.abs_diff_eq(expected.frame, 0.0001));
        let turned_head = Affine3A::from_quat(Quat::from_rotation_y(0.7));
        let stable = tilted
            .update(Duration::from_millis(170), Some(&j), turned_head)
            .unwrap();
        assert!(stable.frame.abs_diff_eq(pad.frame, 0.0001));
    }
    fn gun_joints() -> xr::HandJointLocations {
        let mut j = joints();
        for k in 0..4 {
            j[7 + k].pose.position = xr::Vector3f {
                x: 0.04,
                y: 0.0,
                z: -(k as f32) * 0.025,
            };
        }
        j
    }
    #[test]
    fn finger_gun_cue_matches_click_and_never_sends_axes() {
        for mirror in [1.0, -1.0] {
            let mut j = gun_joints();
            for v in &mut j {
                v.pose.position.x *= mirror;
            }
            let mut p = ThumbPad::default();
            for ms in [0, 100, 149] {
                assert!(
                    p.update(Duration::from_millis(ms), Some(&j), Affine3A::IDENTITY)
                        .is_some_and(|pad| pad.button == Some(false) && pad.axes == Vec2::ZERO)
                );
                assert!(p.owns_hand() && !p.clicked());
            }
            for ms in [150, 200, 250, 350] {
                assert!(
                    p.update(Duration::from_millis(ms), Some(&j), Affine3A::IDENTITY)
                        .is_some_and(|pad| pad.button == Some(true) && pad.axes == Vec2::ZERO)
                );
                assert!(p.clicked() && p.axes == Vec2::ZERO);
            }
            p.update(
                Duration::from_millis(370),
                Some(&joints()),
                Affine3A::IDENTITY,
            );
            assert!(!p.clicked());
            // Re-entering the gun pose must debounce afresh.
            p.update(Duration::from_millis(380), Some(&j), Affine3A::IDENTITY);
            assert!(!p.clicked());
            p.update(Duration::from_millis(480), Some(&j), Affine3A::IDENTITY);
            p.update(Duration::from_millis(530), Some(&j), Affine3A::IDENTITY);
            assert!(p.clicked());
            j[10].location_flags = xr::SpaceLocationFlags::POSITION_VALID;
            p.update(Duration::from_millis(550), Some(&j), Affine3A::IDENTITY);
            assert!(!p.clicked() && !p.owns_hand());
        }
    }
    #[test]
    fn pad_to_click_clears_axes_and_stale_click_must_rearm() {
        let mut p = ThumbPad::default();
        activate(&mut p, &joints());
        let j = gun_joints();
        for ms in [160, 260, 310] {
            assert!(
                p.update(Duration::from_millis(ms), Some(&j), Affine3A::IDENTITY)
                    .is_some_and(|pad| pad.button.is_some() && pad.axes == Vec2::ZERO)
            );
        }
        assert!(p.clicked() && p.local.is_none() && p.axes == Vec2::ZERO);
        p.update(Duration::from_millis(500), Some(&j), Affine3A::IDENTITY);
        assert!(!p.clicked());
        p.update(Duration::from_millis(600), Some(&j), Affine3A::IDENTITY);
        p.update(Duration::from_millis(650), Some(&j), Affine3A::IDENTITY);
        assert!(p.clicked());
        p.update(Duration::from_millis(660), None, Affine3A::IDENTITY);
        assert!(!p.clicked() && !p.owns_hand());
    }
    #[test]
    fn click_cue_returns_translucent_on_thumb_release() {
        let mut p = ThumbPad::default();
        let mut j = gun_joints();
        for ms in [0, 100, 150] {
            p.update(Duration::from_millis(ms), Some(&j), Affine3A::IDENTITY);
        }
        assert!(p.clicked());
        j[5].pose.position = j[2].pose.position;
        let cue = p
            .update(Duration::from_millis(170), Some(&j), Affine3A::IDENTITY)
            .unwrap();
        assert_eq!(cue.button, Some(false));
        assert!(!p.clicked() && cue.axes == Vec2::ZERO);
        assert_eq!(
            command_flags(true, [cue.button.is_none(), false], [p.clicked(), false]),
            1
        );
        p.reset(); // Disabling Sticks/focus loss removes the cue and click.
        assert!(!p.clicked() && !p.owns_hand());
    }
    #[test]
    fn click_packet_flags_are_independent_of_visible_pads() {
        assert_eq!(command_flags(true, [false; 2], [true, false]), 9);
        assert_eq!(command_flags(true, [false; 2], [false, true]), 17);
        assert_eq!(command_flags(true, [true, false], [false, true]), 19);
        assert_eq!(command_flags(false, [true; 2], [true; 2]), 0);
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
            let pad = pads[side].update(
                Duration::from_secs_f64(row["time"].as_f64().unwrap()),
                Some(&j),
                Affine3A::IDENTITY,
            );
            if pads[side].clicked() {
                counts[side] += 1;
                assert!(pad.is_some_and(|p| p.button == Some(true) && p.axes == Vec2::ZERO));
            }
            if let Some(pad) = pad {
                assert!(pad.axes.is_finite() && pad.axes.length() <= 1.0001);
                if !pad.armed {
                    assert!(pad.axes.length() < 0.0001);
                }
                ranges[side][0] = ranges[side][0].min(pad.axes);
                ranges[side][1] = ranges[side][1].max(pad.axes);
            }
        }
        eprintln!("Gun-click replay samples {counts:?}, large-pad axis ranges {ranges:?}");
        assert!(
            counts.iter().all(|n| *n > 20),
            "trace must include both gun poses"
        );
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
