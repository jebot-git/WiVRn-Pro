use std::process::{Child, Command};
use std::sync::Arc;
use std::time::{Duration, Instant};

use glam::{Affine3A, Quat, Vec2, Vec3A};

use idmap_derive::IntegerId;
use smallvec::{SmallVec, smallvec};
use strum::{AsRefStr, EnumIs};
use wayvr_ipc::packet_client::{HandsfreeAction, HandsfreeParams};
use wlx_common::common::LeftRight;
use wlx_common::windowing::{OverlayWindowState, Positioning};

use crate::backend::task::{InputTask, OverlayTask};
use crate::overlays::anchor::{ANCHOR_NAME, GRAB_HELP_NAME};
use crate::overlays::watch::WATCH_NAME;
use crate::state::{AppSession, AppState};
use crate::subsystem::hid::WheelDelta;
use crate::subsystem::input::InputFocus;
use crate::windowing::backend::OverlayEventData;
use crate::windowing::manager::OverlayWindowManager;
use crate::windowing::window::{self, OverlayCategory, OverlayWindowData, realign, scalar_scale};
use crate::windowing::{OverlayID, OverlaySelector, raycast_overlay};

use super::task::TaskType;

#[derive(Clone, Default)]
pub struct HoverResult {
    pub haptics: Option<Haptics>,
    /// If true, the laser shows at this position and no further raycasting will be done.
    pub consume: bool,
}

impl HoverResult {
    pub fn consume() -> Self {
        HoverResult {
            consume: true,
            ..Default::default()
        }
    }
}

pub struct TrackedDevice {
    pub soc: Option<f32>,
    pub charging: bool,
    pub role: TrackedDeviceRole,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq, IntegerId, AsRefStr)]
pub enum TrackedDeviceRole {
    None,
    Hmd,
    LeftHand,
    RightHand,
    Tracker,
}

#[derive(Debug, Eq, PartialEq, Clone, Copy, EnumIs)]
pub enum FocusPickState {
    None,
    /// aiming to pick the focus
    Aiming,
    /// picking the focus. this will be consumed on valid on_hover
    Picking,
}

pub struct InputState {
    pub hmd: Affine3A,
    pub frame_view: Option<Affine3A>,
    pub ipd: f32,
    pub eye_gaze: Option<Affine3A>,
    pub pointers: [Pointer; 2],
    pub devices: Vec<TrackedDevice>,
    pub handsfree_state: PointerState,
    pub picking_focus: FocusPickState,
    processes: Vec<Child>,
    disable_lerp_until: Instant,
    /// The watch hand is the offhand for the hand-tracked space-drag chord.
    pub space_drag_hand: usize,
    pub mouse_priority: crate::mouse_priority::MousePriority,
    pub joysticks_enabled: bool,
    pub hand_controllers_enabled: bool,
    pub hand_tracking_enabled: bool,
    pub walking_enabled: bool,
    pub body_turning_enabled: bool,
    pub head_yaw_total: f64,
    head_last_yaw: Option<f64>,
    two_hand_grab: Option<(OverlayID, crate::hand_resize::HandResize)>,
}

impl InputState {
    pub fn new() -> Self {
        Self {
            hmd: Affine3A::IDENTITY,
            frame_view: None,
            ipd: 0.0,
            eye_gaze: None,
            pointers: [Pointer::new(0), Pointer::new(1)],
            devices: Vec::new(),
            processes: Vec::new(),
            handsfree_state: PointerState::default(),
            picking_focus: FocusPickState::None,
            disable_lerp_until: Instant::now(),
            space_drag_hand: 0,
            mouse_priority: Default::default(),
            joysticks_enabled: false,
            hand_controllers_enabled: false,
            hand_tracking_enabled: true,
            walking_enabled: false,
            body_turning_enabled: false,
            head_yaw_total: 0.0,
            head_last_yaw: None,
            two_hand_grab: None,
        }
    }

    pub fn should_disable_lerp(&self) -> bool {
        self.disable_lerp_until > Instant::now()
    }

    pub fn stop_picking(&mut self) {
        if !self.picking_focus.is_none() {
            self.picking_focus = FocusPickState::None;
            self.disable_lerp_until = Instant::now() + Duration::from_millis(250);
        }
    }

    fn update_head_rot(&mut self) {
        let orientation = Quat::from_mat3a(&self.hmd.matrix3);

        let qy = orientation.y as f64;
        let qw = orientation.w as f64;

        if qy * qy + qw * qw < 1e-10 {
            debug_assert!(false);
            return;
        }

        let yaw = 2.0 * qy.atan2(qw);
        if !yaw.is_finite() {
            debug_assert!(false);
            return;
        }

        let Some(last) = self.head_last_yaw else {
            self.head_last_yaw = Some(yaw);
            return;
        };

        let mut delta = (yaw - last).rem_euclid(std::f64::consts::TAU);
        if delta > std::f64::consts::PI {
            delta -= std::f64::consts::TAU;
        }

        if delta.abs() <= std::f64::consts::PI / 4.0 {
            self.head_yaw_total -= delta;
        }

        self.head_last_yaw = Some(yaw);
    }

    pub fn apply_handsfree_action(&mut self, params: HandsfreeParams) {
        const fn set_true(v: &mut bool) {
            *v = true;
        }
        const fn set_false(v: &mut bool) {
            *v = false;
        }
        const fn toggle(v: &mut bool) {
            *v = !*v;
        }

        let (action, apply) = match params {
            HandsfreeParams::SetMode(_) => {
                return;
            }
            HandsfreeParams::Press(action) => (action, set_true as fn(&mut bool)),
            HandsfreeParams::Release(action) => (action, set_false as fn(&mut bool)),
            HandsfreeParams::Toggle(action) => (action, toggle as fn(&mut bool)),
            HandsfreeParams::Scroll(val) => {
                self.handsfree_state.scroll_y = val;
                return;
            }
        };

        match action {
            HandsfreeAction::Click => apply(&mut self.handsfree_state.click),
            HandsfreeAction::RightModifier => apply(&mut self.handsfree_state.click_modifier_right),
            HandsfreeAction::MiddleModifier => {
                apply(&mut self.handsfree_state.click_modifier_middle);
            }
            HandsfreeAction::Grab => apply(&mut self.handsfree_state.grab),
        }
    }

    pub fn handle_task(&mut self, task: InputTask) {
        match task {
            InputTask::Haptics { device, haptics } => {
                if let Some(pointer) = self.pointers.get_mut(device) {
                    pointer.pending_haptics = Some(haptics);
                } else {
                    log::warn!("Can't trigger haptics on non-existing device: {device}");
                }
            }
        }
    }

    pub const fn pre_update(&mut self) {
        self.pointers[0].before = self.pointers[0].now;
        self.pointers[1].before = self.pointers[1].now;
    }

    pub fn post_update(&mut self, session: &AppSession) {
        self.update_head_rot();

        for hand in &mut self.pointers {
            #[cfg(debug_assertions)]
            debug_print_hand(hand);

            if hand.now.click {
                hand.last_click = Instant::now();
            }

            // Prevent the mode from changing during a click
            if !hand.before.click {
                if hand.now.click_modifier_right {
                    hand.interaction.mode = PointerMode::Right;
                    continue;
                }

                if hand.now.click_modifier_middle {
                    hand.interaction.mode = PointerMode::Middle;
                    continue;
                }

                let hmd_up = self.hmd.transform_vector3a(Vec3A::Y);
                let dot = hmd_up.dot(hand.pose.transform_vector3a(Vec3A::X))
                    * 2.0f32.mul_add(-(hand.idx as f32), 1.0);

                hand.interaction.mode = if dot < -0.85 {
                    PointerMode::Right
                } else if dot > 0.7 {
                    PointerMode::Middle
                } else {
                    PointerMode::Left
                };

                let middle_click_orientation = false;
                let right_click_orientation = false;
                match hand.interaction.mode {
                    PointerMode::Middle => {
                        if !middle_click_orientation {
                            hand.interaction.mode = PointerMode::Left;
                        }
                    }
                    PointerMode::Right if !right_click_orientation => {
                        hand.interaction.mode = PointerMode::Left;
                    }
                    _ => {}
                }
            }

            if hand.now.alt_click != hand.before.alt_click {
                // Reap previous processes
                self.processes
                    .retain_mut(|child| !matches!(child.try_wait(), Ok(Some(_))));

                let mut args = if hand.now.alt_click {
                    session.config.alt_click_down.iter()
                } else {
                    session.config.alt_click_up.iter()
                };

                if let Some(program) = args.next()
                    && let Ok(child) = Command::new(program).args(args).spawn()
                {
                    self.processes.push(child);
                }
            }
        }
    }
}

#[cfg(debug_assertions)]
fn debug_print_hand(hand: &Pointer) {
    {
        if hand.now.click != hand.before.click {
            log::debug!("Hand {}: click {}", hand.idx, hand.now.click);
        }
        if hand.now.grab != hand.before.grab {
            log::debug!("Hand {}: grab {}", hand.idx, hand.now.grab);
        }
        if hand.now.grab_float != hand.before.grab_float {
            log::debug!("Hand {}: grab {}", hand.idx, hand.now.grab);
        }
        if hand.now.alt_click != hand.before.alt_click {
            log::debug!("Hand {}: alt_click {}", hand.idx, hand.now.alt_click);
        }
        if hand.now.show_hide != hand.before.show_hide {
            log::debug!("Hand {}: show_hide {}", hand.idx, hand.now.show_hide);
        }
        if hand.now.toggle_dashboard != hand.before.toggle_dashboard {
            log::debug!(
                "Hand {}: toggle_dashboard {}",
                hand.idx,
                hand.now.toggle_dashboard
            );
        }
        if hand.now.space_drag != hand.before.space_drag {
            log::debug!("Hand {}: space_drag {}", hand.idx, hand.now.space_drag);
        }
        if hand.now.space_rotate != hand.before.space_rotate {
            log::debug!("Hand {}: space_rotate {}", hand.idx, hand.now.space_rotate);
        }
        if hand.now.space_reset != hand.before.space_reset {
            log::debug!("Hand {}: space_reset {}", hand.idx, hand.now.space_reset);
        }
        if hand.now.click_modifier_right != hand.before.click_modifier_right {
            log::debug!(
                "Hand {}: click_modifier_right {}",
                hand.idx,
                hand.now.click_modifier_right
            );
        }
        if hand.now.click_modifier_middle != hand.before.click_modifier_middle {
            log::debug!(
                "Hand {}: click_modifier_middle {}",
                hand.idx,
                hand.now.click_modifier_middle
            );
        }
    }
}

pub struct InteractionState {
    pub mode: PointerMode,
    pub grabbed: Option<GrabData>,
    pub clicked_id: Option<OverlayID>,
    pub hovered_id: Option<OverlayID>,
    pub should_block_input: bool,
    pub should_block_poses: bool,
    pub kbd_block_activated: bool,
}

impl Default for InteractionState {
    fn default() -> Self {
        Self {
            mode: PointerMode::Left,
            grabbed: None,
            clicked_id: None,
            hovered_id: None,
            should_block_input: false,
            should_block_poses: false,
            kbd_block_activated: false,
        }
    }
}

pub struct Pointer {
    pub domed_owned: bool,
    pub domed_release_gate: bool,
    pub idx: usize,
    pub pose: Affine3A,
    pub raw_pose: Affine3A,
    pub now: PointerState,
    pub before: PointerState,
    pub last_click: Instant,
    pub pending_haptics: Option<Haptics>,
    pub(super) interaction: InteractionState,
    pub tracked: bool,
    pub handsfree: bool,
    pub hand_tracking: bool,
    pub frame_hand: Option<crate::hands_frame::Hand>,
    pub wrist_pose: Option<Affine3A>,
    pub index_tip: Option<Vec3A>,
    /// Thumb, index and middle tips, in stage metres.
    pub keyboard_tips: Option<[Vec3A; 3]>,
    pub keyboard_radii: [f32; 3],
    pub keyboard_neutral: bool,
    pub(crate) keyboard_contact: Option<RayHit>,
    pub keyboard_input_armed: bool,
    pub keyboard_hover: bool,
    keyboard_projection_only: bool,
    keyboard_grab_target: Option<(RayHit, Instant)>,
    #[cfg(feature = "openxr")]
    pub joystick_pad: Option<crate::hand_joystick::Pad>,
    pub joystick_owned: bool,
    touch_hit: Option<RayHit>,
    touch_press: crate::hand_touch::TouchPress,
    touch_needs_neutral: bool,
    pub cancel_interaction: bool,
    hand_grab_needs_release: bool,
    /// distance of the most recent ray hit on a wayland client window, used for drag n drop
    pub last_wvr_hit_distance: Option<f32>,
}

impl Pointer {
    pub fn frame_blocked(&self) -> bool { self.interaction.grabbed.is_some() || self.keyboard_hover || self.joystick_owned || self.cancel_interaction }
    pub fn new(idx: usize) -> Self {
        debug_assert!(idx == 0 || idx == 1);
        Self {
            idx,
            pose: Affine3A::IDENTITY,
            raw_pose: Affine3A::IDENTITY,
            now: PointerState::default(),
            before: PointerState::default(),
            last_click: Instant::now(),
            pending_haptics: None,
            interaction: InteractionState::default(),
            tracked: false,
            domed_owned:false,
            domed_release_gate:false,
            handsfree: false,
            hand_tracking: false,
            frame_hand: None,
            wrist_pose: None,
            index_tip: None,
            keyboard_tips: None,
            keyboard_radii: [0.014; 3],
            keyboard_neutral: false,
            keyboard_contact: None,
            keyboard_input_armed: false,
            keyboard_hover: false,
            keyboard_projection_only: false,
            keyboard_grab_target: None,
            #[cfg(feature = "openxr")]
            joystick_pad: None,
            joystick_owned: false,
            touch_hit: None,
            touch_press: Default::default(),
            touch_needs_neutral: false,
            cancel_interaction: false,
            hand_grab_needs_release: false,
            last_wvr_hit_distance: None,
        }
    }

    pub fn attachment_pose(&self) -> Affine3A {
        self.wrist_pose.map_or(self.pose, |wrist| {
            // Map the existing controller attachment frame onto the back of
            // the wrist, preserving saved offsets and scale in that frame.
            let watch = Affine3A::from_rotation_translation(
                crate::overlays::watch::WATCH_ROT,
                crate::overlays::watch::WATCH_POS,
            );
            let on_wrist = Affine3A::from_rotation_translation(
                Quat::from_rotation_x(-std::f32::consts::FRAC_PI_2)
                    * Quat::from_rotation_z(std::f32::consts::FRAC_PI_2),
                glam::Vec3::new(0.0, 0.035, 0.025),
            );
            wrist * on_wrist * watch.inverse()
        })
    }

    pub const fn hand(&self) -> Option<LeftRight> {
        match self.idx {
            0 => Some(LeftRight::Left),
            1 => Some(LeftRight::Right),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, Default)]
pub struct PointerState {
    pub scroll_x: f32,
    pub scroll_y: f32,
    pub click: bool,
    pub grab: bool,
    pub grab_float: bool,
    pub alt_click: bool,
    pub show_hide: bool,
    pub toggle_dashboard: bool,
    pub space_drag: bool,
    pub space_rotate: bool,
    pub space_reset: bool,
    pub click_modifier_right: bool,
    pub click_modifier_middle: bool,
    pub move_mouse: bool,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct PointerHit {
    pub pointer: usize,
    pub overlay: OverlayID,
    pub mode: PointerMode,
    pub primary: bool,
    pub uv: Vec2,
}

#[derive(Clone, Copy)]
pub struct Haptics {
    pub intensity: f32,
    pub duration: f32,
    pub frequency: f32,
}

#[derive(Debug, Clone, Copy, Default)]
pub(crate) struct RayHit {
    overlay: OverlayID,
    global_pos: Vec3A,
    local_pos: Vec2,
    dist: f32,
}

#[derive(Debug, Clone, Copy, Default)]
pub struct GrabData {
    pub offset: Affine3A,
    pub grabbed_id: OverlayID,
    pub grab_anchor: bool,
}

#[repr(u8)]
#[derive(Debug, Clone, Copy, Default, Eq, PartialEq)]
pub enum PointerMode {
    #[default]
    Left,
    Right,
    Middle,
    Special,
}

pub struct PointerLine {
    pub mode: PointerMode,
    pub a: Vec3A,
    pub b: Vec3A,
}

fn populate_lines(
    lines: &mut Vec<PointerLine>,
    pointer: &Pointer,
    maybe_hit: Option<(PointerHit, RayHit)>,
    hmd: &Affine3A,
) {
    if pointer.touch_hit.is_some() || (pointer.hand_tracking && pointer.keyboard_contact.is_some() && !pointer.now.grab) {
        return;
    }
    let Some((hit, raw_hit)) = maybe_hit else {
        return;
    };

    if pointer.handsfree {
        const HALF_SIZE: f32 = 0.01;

        // horizontal arm
        lines.push(PointerLine {
            mode: hit.mode,
            a: raw_hit.global_pos - (hmd.x_axis * HALF_SIZE),
            b: raw_hit.global_pos + (hmd.x_axis * HALF_SIZE),
        });

        // vertical arm
        lines.push(PointerLine {
            mode: PointerMode::Special,
            a: raw_hit.global_pos - (hmd.y_axis * HALF_SIZE),
            b: raw_hit.global_pos + (hmd.y_axis * HALF_SIZE),
        });
    } else {
        lines.push(PointerLine {
            mode: hit.mode,
            a: pointer.pose.translation,
            b: raw_hit.global_pos,
        });
    }
}

fn update_focus(app: &mut AppState, overlay_input_focus: Option<InputFocus>) {
    if let Some(f) = &overlay_input_focus
        && app.input_state.picking_focus.is_none()
        && app
            .hid_provider
            .set_input_focus(app.wvr_server.as_mut(), *f)
    {
        log::debug!("Setting keyboard focus to {:?}", *f);
    }
}

pub fn interact<O>(
    overlays: &mut OverlayWindowManager<O>,
    app: &mut AppState,
    lines: &mut Vec<PointerLine>,
) -> [Option<Haptics>; 2]
where
    O: Default,
{
    route_domed(overlays,app);
    if app.watch_menu.open().is_some() {
        let watch_visible = app.session.config.enable_watch && overlays.values().any(|o|
            o.config.name.as_ref() == WATCH_NAME && o.config.active_state.as_ref().is_some_and(|s| s.alpha >= 0.05));
        if !watch_visible { app.watch_menu.close(); }
        else {
            app.input_state.two_hand_grab = None;
            for idx in 0..2 {
                if app.input_state.pointers[idx].interaction.grabbed.is_some() {
                    release_pointer(idx, overlays, app);
                }
            }
        }
    }
    if crate::hands_frame_panel::update(overlays, app) {
        app.input_state.two_hand_grab = None;
        for idx in 0..2 {
            release_pointer(idx, overlays, app);
            app.input_state.pointers[idx].interaction.should_block_input = true;
            app.input_state.pointers[idx].interaction.should_block_poses = false;
        }
        return [None,None];
    }
    if update_two_hand_grab(overlays, app) {
        return [None, None];
    }
    let hits = if app.input_state.pointers[1].last_click > app.input_state.pointers[0].last_click {
        let right = interact_hand(1, overlays, app);
        let left = interact_hand(0, overlays, app);
        [left, right]
    } else {
        let left = interact_hand(0, overlays, app);
        let right = interact_hand(1, overlays, app);
        [left, right]
    };

    for (idx, hit) in hits.iter().enumerate() {
        if app.input_state.pointers[idx].domed_owned {
            if let Some(distance)=app.domed.hit(idx) {let p=&app.input_state.pointers[idx];lines.push(PointerLine{mode:p.interaction.mode,a:p.pose.translation,b:p.pose.transform_point3a(Vec3A::NEG_Z*distance)});}
            continue;
        }
        populate_lines(
            lines,
            &app.input_state.pointers[idx],
            hit.0,
            &app.input_state.hmd,
        );
    }

    crate::overlays::drag_item::update_position(overlays, app);

    [hits[0].1, hits[1].1]
}

// Middle pinch leaves the selecting index fingertip free, as in the reference.
fn keyboard_pointer_mode(now: PointerState, before: PointerState) -> PointerMode {
    if now.click_modifier_middle || (!now.click && before.click_modifier_middle) {
        PointerMode::Left
    } else if now.click_modifier_right || (!now.click && before.click_modifier_right) {
        PointerMode::Right
    } else if now.click || before.click {
        PointerMode::Middle
    } else {
        PointerMode::Left
    }
}

/// A fist moves the selecting fingertip; retain its last keyboard target only
/// through the short gesture debounce. Tracking/source loss clears this cache.
fn keyboard_grab_candidate(pointer: &Pointer, now: Instant) -> Option<RayHit> {
    let (mut hit, at) = pointer.keyboard_grab_target?;
    if now.saturating_duration_since(at) > Duration::from_millis(250) {
        return None;
    }
    hit.dist = 0.;
    Some(hit)
}

fn keyboard_hit(
    pointer: &Pointer,
    id: OverlayID,
    transform: &Affine3A,
    curvature: Option<&f32>,
    grabbable: bool,
    edit_mode: bool,
) -> Option<RayHit> {
    if pointer.hand_tracking
        && pointer.now.grab
        && !pointer.before.grab
        && grabbable
        && let Some(hit) = keyboard_grab_candidate(pointer, Instant::now())
        && hit.overlay == id
    {
        return Some(hit);
    }
    if pointer.hand_tracking && !pointer.now.grab && !edit_mode {
        pointer.keyboard_contact.filter(|hit| hit.overlay == id)
    } else {
        ray_test(&pointer.pose, id, transform, curvature)
    }
}

/// Position-only selection: hand rotation cannot move the keyboard cursor.
fn keyboard_projection(
    tip: Vec3A,
    id: OverlayID,
    transform: &Affine3A,
    curvature: Option<&f32>,
) -> Option<RayHit> {
    let (_, rotation, _) = transform.to_scale_rotation_translation();
    let normal = transform.z_axis.try_normalize()?;
    let origin = Affine3A::from_rotation_translation(rotation, (tip + normal * 0.3).into());
    let mut hit = ray_test(&origin, id, transform, curvature)?;
    let clearance = hit.dist - 0.3;
    if !(-0.08..=0.25).contains(&clearance) {
        return None;
    }
    hit.dist = clearance.abs();
    Some(hit)
}

/// All three fingers must be near the surface. Selection lies between their
/// contact spheres and is independent of hand rotation and the pointer laser.
fn keyboard_contacts(
    tips: [Vec3A; 3],
    id: OverlayID,
    transform: &Affine3A,
    curvature: Option<&f32>,
) -> Option<RayHit> {
    for tip in tips {
        if !tip.is_finite() { return None; }
        keyboard_projection(tip, id, transform, curvature)?;
    }
    keyboard_projection((tips[0] + tips[1] + tips[2]) / 3., id, transform, curvature)
}

fn keyboard_can_press(pointer: &Pointer) -> bool {
    !pointer.hand_tracking || (pointer.keyboard_contact.is_some()
        && pointer.keyboard_input_armed && !pointer.now.grab)
}

/// Returns the previously pressed surface to release on departure. Re-entry
/// requires physically open fingers, even during the gesture detector's debounce.
fn update_keyboard_contact(pointer: &mut Pointer, target: Option<RayHit>) -> Option<RayHit> {
    let previous = pointer.keyboard_contact;
    let mut release = None;
    if previous.map(|h| h.overlay) != target.map(|h| h.overlay) {
        pointer.keyboard_input_armed = false;
        if let Some(old) = previous && pointer.interaction.clicked_id == Some(old.overlay) {
            pointer.interaction.clicked_id = None;
            release = Some(old);
        }
    }
    pointer.keyboard_contact = target;
    if target.is_some() && pointer.keyboard_neutral && !pointer.now.click && !pointer.before.click {
        pointer.keyboard_input_armed = true;
    }
    if let Some(target) = target {
        pointer.keyboard_grab_target = Some((target, Instant::now()));
    }
    release
}

/// Project an index fingertip onto the same flat/curved surface used by rays.
fn touch_projection(
    tip: Vec3A,
    id: OverlayID,
    transform: &Affine3A,
    curvature: Option<&f32>,
) -> Option<(RayHit, f32)> {
    let (_, rotation, _) = transform.to_scale_rotation_translation();
    let normal = transform.z_axis.try_normalize()?;
    let origin = Affine3A::from_rotation_translation(rotation, (tip + normal * 0.05).into());
    let hit = ray_test(&origin, id, transform, curvature)?;
    let clearance = hit.dist - 0.05;
    (-0.020..=0.035)
        .contains(&clearance)
        .then_some((hit, clearance))
}

fn prepare_touch<O: Default>(
    idx: usize,
    overlays: &mut OverlayWindowManager<O>,
    app: &mut AppState,
) {
    let pointer = &app.input_state.pointers[idx];
    let old = pointer.touch_hit;
    let mut keyboard_target = None;
    if pointer.hand_tracking
        && !pointer.now.grab
        && !overlays.get_edit_mode()
        && pointer.interaction.grabbed.is_none()
        && let Some(tips) = pointer.keyboard_tips
    {
        let ids: Vec<_> = overlays.iter().filter_map(|(id, overlay)| {
            (overlay.config.category == OverlayCategory::Keyboard).then_some(id)
        }).collect();
        for id in ids {
            let overlay = overlays.mut_by_id(id).unwrap();
            if app.watch_menu.open().is_some() { continue; }
            if !overlay.config.room_available(&app.room) { continue; }
            let Some(state) = overlay.config.active_state.as_ref() else { continue; };
            if !state.interactable || state.alpha < 0.025 { continue; }
            let transform = state.transform * overlay.config.backend.frame_meta()
                .map(|meta| meta.transform).unwrap_or_default();
            if let Some(hit) = keyboard_contacts(tips, id, &transform, state.curvature.as_ref())
                && let Some(t) = overlay.config.backend.get_interaction_transform()
            {
                let uv = t.transform_point2(hit.local_pos);
                if (0.0..=1.0).contains(&uv.x) && (0.0..=1.0).contains(&uv.y)
                    && keyboard_target.is_none_or(|old: RayHit| hit.dist < old.dist)
                {
                    keyboard_target = Some(hit);
                }
            }
        }
    }
    let pointer = &mut app.input_state.pointers[idx];
    if let Some(old) = update_keyboard_contact(pointer, keyboard_target) {
        let hit = PointerHit { pointer: idx, overlay: old.overlay,
            mode: keyboard_pointer_mode(pointer.now, pointer.before), ..Default::default() };
        if let Some(overlay) = overlays.mut_by_id(old.overlay) {
            overlay.config.backend.on_pointer(app, &hit, false);
        }
    }
    let pointer = &mut app.input_state.pointers[idx];
    // Keep the low-hand gesture detector alive while the fingertip curls into a fist.
    let closing_fist = pointer.hand_tracking
        && pointer.index_tip.is_some()
        && pointer.wrist_pose.is_some()
        && keyboard_grab_candidate(pointer, Instant::now()).is_some();
    pointer.keyboard_hover = keyboard_target.is_some() || closing_fist;
    pointer.keyboard_projection_only = pointer.keyboard_hover && !pointer.tracked;
    pointer.tracked |= pointer.keyboard_hover;
    let mut candidates = Vec::new();
    if pointer.hand_tracking
        && !pointer.now.grab
        && pointer.interaction.grabbed.is_none()
        && let Some(tip) = pointer.index_tip
    {
        for (id, overlay) in overlays.iter() {
            // The hand keyboard uses hover plus pinch, never depth presses.
            if overlay.config.name.as_ref() != WATCH_NAME {
                continue;
            }
            if app.watch_menu.open().is_some() && overlay.config.name.as_ref() != WATCH_NAME { continue; }
            if !overlay.config.room_available(&app.room) { continue; }
            let Some(state) = overlay.config.active_state.as_ref() else {
                continue;
            };
            if !state.interactable || state.alpha < 0.025 {
                continue;
            }
            if overlay.config.name.as_ref() == WATCH_NAME
                && matches!(state.positioning, Positioning::FollowHand {hand, ..} if hand as usize == idx)
            {
                continue;
            }
            if let Some(hit) = touch_projection(tip, id, &state.transform, state.curvature.as_ref())
            {
                candidates.push(hit);
            }
        }
    }
    candidates.sort_by(|a, b| a.1.abs().total_cmp(&b.1.abs()));
    let target = candidates.into_iter().find(|(hit, _)| {
        overlays
            .mut_by_id(hit.overlay)
            .and_then(|o| o.config.backend.get_interaction_transform())
            .is_some_and(|t| {
                let uv = t.transform_point2(hit.local_pos);
                (0.0..=1.0).contains(&uv.x) && (0.0..=1.0).contains(&uv.y)
            })
    });
    let changed = old.map(|h| h.overlay) != target.map(|(h, _)| h.overlay);
    if changed
        && (old.is_some() || (target.is_some() && app.input_state.pointers[idx].before.click))
    {
        // Send button-up to the old target before changing input source.
        release_pointer(idx, overlays, app);
        app.input_state.pointers[idx].touch_needs_neutral = true;
    }
    let pointer = &mut app.input_state.pointers[idx];
    if let Some((hit, clearance)) = target {
        if changed {
            pointer.touch_press = Default::default();
        }
        pointer.touch_hit = Some(hit);
        pointer.tracked = true; // Touch also works below the ray's pointing zone.
        pointer.now = PointerState {
            click: pointer.touch_press.update(Instant::now(), clearance),
            ..Default::default()
        };
        pointer.interaction.mode = PointerMode::Left;
        pointer.touch_needs_neutral = true;
    } else if pointer.touch_needs_neutral {
        if !pointer.now.click && !pointer.now.grab {
            pointer.touch_needs_neutral = false;
        }
        pointer.now = PointerState::default();
    }
}

/// Release at the previous pose before switching sources or losing tracking.
pub fn release_pointer<O>(idx: usize, overlays: &mut OverlayWindowManager<O>, app: &mut AppState)
where
    O: Default,
{
    let pointer = &mut app.input_state.pointers[idx];
    pointer.now = PointerState::default();
    pointer.cancel_interaction = false;
    pointer.tracked = false;
    // A lowered hand can still have a valid wrist attachment.
    pointer.pending_haptics = None;
    pointer.touch_hit = None;
    pointer.keyboard_hover = false;
    pointer.keyboard_contact = None;
    pointer.keyboard_input_armed = false;
    pointer.keyboard_projection_only = false;
    pointer.keyboard_grab_target = None;
    pointer.touch_press = Default::default();
    let grabbed = pointer.interaction.grabbed.map(|g| g.grabbed_id);
    let hovered = pointer.interaction.hovered_id.take();
    if let Some(id) = grabbed {
        if let Some(overlay) = overlays.mut_by_id(id) {
            handle_grabbed(idx, overlay, app);
        }
        app.input_state.pointers[idx].interaction.grabbed = None;
    }
    handle_no_hit(idx, hovered, overlays, app);
    if let Some(server) = app.wvr_server.as_mut() {
        if !(app.input_state.pointers[idx].hand_tracking && app.input_state.mouse_priority.active())
        {
            server.clear_dnd_focus(idx);
        }
    }
    app.input_state.pointers[idx].before = PointerState::default();
}

fn interact_hand<O>(
    idx: usize,
    overlays: &mut OverlayWindowManager<O>,
    app: &mut AppState,
) -> (Option<(PointerHit, RayHit)>, Option<Haptics>)
where
    O: Default,
{
    if app.input_state.pointers[idx].domed_owned {return (None,None);}
    if app.input_state.pointers[idx].domed_release_gate {
        if app.input_state.pointers[idx].now.click||app.input_state.pointers[idx].now.grab {app.input_state.pointers[idx].now=PointerState::default();}
        else {app.input_state.pointers[idx].domed_release_gate=false;}
    }
    if app.input_state.pointers[idx].hand_tracking && app.input_state.pointers[idx].now.space_drag {
        // Playspace movement owns this hand; release touch/click/grab targets
        // and do not move the desktop cursor while dragging the playspace.
        release_pointer(idx, overlays, app);
        app.input_state.pointers[idx].now.space_drag = true;
        app.input_state.pointers[idx].tracked = true;
        return (None, None);
    }
    if app.input_state.pointers[idx].joystick_owned
        || (app.input_state.pointers[idx].hand_tracking && app.input_state.mouse_priority.active())
    {
        release_pointer(idx, overlays, app);
        app.input_state.pointers[idx].touch_needs_neutral = true;
        return (None, None);
    }
    if !app.input_state.pointers[idx].cancel_interaction {
        prepare_touch(idx, overlays, app);
    }
    if app.input_state.pointers[idx].cancel_interaction || !app.input_state.pointers[idx].tracked {
        release_pointer(idx, overlays, app);
        return (None, None);
    }
    let mut pointer = &mut app.input_state.pointers[idx];
    let pending_haptics = pointer.pending_haptics.take();

    // already grabbing, ignore everything else
    if let Some(grab_data) = pointer.interaction.grabbed {
        if let Some(grabbed) = overlays.mut_by_id(grab_data.grabbed_id) {
            handle_grabbed(idx, grabbed, app);
        } else {
            log::warn!("Grabbed overlay {:?} does not exist", grab_data.grabbed_id);
            pointer.interaction.grabbed = None;
        }
        return (None, pending_haptics);
    }

    let hovered_id = pointer.interaction.hovered_id.take();
    let (Some((mut hit, raw_hit)), haptics) = get_nearest_hit(idx, overlays, app) else {
        if let Some(wvr_server) = app.wvr_server.as_mut() {
            wvr_server.clear_dnd_focus(idx);
        }
        handle_no_hit(idx, hovered_id, overlays, app);
        return (None, pending_haptics); // no hit
    };

    let hit_is_wayvr = overlays
        .iter()
        .find(|(id, _)| *id == hit.overlay)
        .is_some_and(|(_, overlay)| overlay.config.category == OverlayCategory::WayVR);

    if hit_is_wayvr {
        app.input_state.pointers[idx].last_wvr_hit_distance = Some(raw_hit.dist);
    } else if let Some(wvr_server) = app.wvr_server.as_mut() {
        wvr_server.clear_dnd_focus(idx);
    }

    // focus change
    if let Some(hovered_id) = hovered_id
        && hovered_id != hit.overlay
        && let Some(old_hovered) = overlays.mut_by_id(hovered_id)
    {
        if old_hovered.primary_pointer.is_some_and(|i| i == idx) {
            old_hovered.primary_pointer = None;
        }
        log::debug!("{} on_left (focus changed)", old_hovered.config.name);
        old_hovered.config.backend.on_left(app, idx);
        old_hovered.hover_pointers[idx] = false;
        if !old_hovered.hover_pointers.iter().any(|x| *x) {
            overlays.edit_overlay(hovered_id, false, app);
        }
    }

    overlays.edit_overlay(hit.overlay, true, app);
    let edit_mode = overlays.get_edit_mode();

    let Some(hovered) = overlays.mut_by_id(hit.overlay) else {
        log::warn!("Hit overlay {:?} does not exist", hit.overlay);
        return (None, pending_haptics); // no hit
    };
    pointer = &mut app.input_state.pointers[idx];
    pointer.interaction.hovered_id = Some(hit.overlay);
    hovered.hover_pointers[idx] = true;

    if let Some(primary_pointer) = hovered.primary_pointer {
        if hit.pointer <= primary_pointer {
            hovered.primary_pointer = Some(hit.pointer);
            hit.primary = true;
        }
    } else {
        hovered.primary_pointer = Some(hit.pointer);
        hit.primary = true;
    }

    if let Some(state) = hovered.config.active_state.as_ref() {
        pointer.interaction.should_block_input = state.block_input
            && (hovered.config.name.as_ref() != WATCH_NAME
                || !app.session.config.block_game_input_ignore_watch);

        pointer.interaction.should_block_poses = state.block_input
            && app.session.config.block_poses_on_kbd_interaction
            && hovered.config.category == OverlayCategory::Keyboard
            && pointer.interaction.kbd_block_activated;
    } else {
        pointer.interaction.should_block_input = false;
        pointer.interaction.should_block_poses = false;
    }

    #[cfg(debug_assertions)]
    log::trace!("Hit: {} {:?}", hovered.config.name, hit);

    let hovered_state = hovered.config.active_state.as_mut().unwrap();

    let grab_float = pointer.now.grab_float;
    let grab_start = pointer.now.grab && !pointer.before.grab;

    // grab
    if grab_start && hovered_state.grabbable {
        update_focus(app, hovered.config.input_focus);
        start_grab(StartGrabParams {
            idx,
            id: hit.overlay,
            name: hovered.config.name.clone(),
            editing: hovered.config.editing,
            state: hovered_state,
            app,
            edit_mode,
            grab_float,
        });
        log::debug!("Hand {}: grabbed {}", hit.pointer, hovered.config.name);
        return (
            Some((hit, raw_hit)),
            Some(Haptics {
                intensity: 0.25,
                duration: 0.1,
                frequency: 0.1,
            }),
        );
    }

    handle_scroll(&hit, hovered, app);

    // click / release
    let pointer = &mut app.input_state.pointers[hit.pointer];
    if pointer.now.click && !pointer.before.click
        && (hovered.config.category != OverlayCategory::Keyboard || keyboard_can_press(pointer))
    {
        pointer.interaction.clicked_id = Some(hit.overlay);
        if hovered.config.category == OverlayCategory::Keyboard {
            pointer.interaction.kbd_block_activated = true;
        }
        update_focus(app, hovered.config.input_focus);
        hovered.config.backend.on_pointer(app, &hit, true);
    } else if !pointer.now.click && pointer.before.click {
        // send release event to overlay that was originally clicked
        if let Some(clicked_id) = pointer.interaction.clicked_id.take() {
            if let Some(clicked) = overlays.mut_by_id(clicked_id) {
                clicked.config.backend.on_pointer(app, &hit, false);
            }
        } else {
            hovered.config.backend.on_pointer(app, &hit, false);
        }
    }

    (Some((hit, raw_hit)), haptics.or(pending_haptics))
}

fn handle_no_hit<O>(
    pointer_idx: usize,
    hovered_id: Option<OverlayID>,
    overlays: &mut OverlayWindowManager<O>,
    app: &mut AppState,
) {
    if let Some(hovered_id) = hovered_id
        && let Some(hovered) = overlays.mut_by_id(hovered_id)
    {
        if hovered.primary_pointer == Some(pointer_idx) {
            hovered.primary_pointer = None;
        }
        log::debug!("{} on_left (no hit)", hovered.config.name);
        hovered.config.backend.on_left(app, pointer_idx);
        hovered.hover_pointers[pointer_idx] = false;
        if !hovered.hover_pointers.iter().any(|x| *x) {
            overlays.edit_overlay(hovered_id, false, app);
        }
    }

    let pointer = &mut app.input_state.pointers[pointer_idx];
    pointer.interaction.should_block_input = false;
    pointer.interaction.should_block_poses = false;
    pointer.interaction.kbd_block_activated = false;

    // in case click released while not aiming at anything
    // send release event to overlay that was originally clicked
    if !pointer.now.click
        && pointer.before.click
        && let Some(clicked_id) = pointer.interaction.clicked_id.take()
        && let Some(clicked) = overlays.mut_by_id(clicked_id)
    {
        let hit = PointerHit {
            pointer: pointer.idx,
            overlay: clicked_id,
            mode: pointer.interaction.mode,
            ..Default::default()
        };
        clicked.config.backend.on_pointer(app, &hit, false);
    }
}

fn handle_scroll<O>(hit: &PointerHit, hovered: &mut OverlayWindowData<O>, app: &mut AppState) {
    let pointer = &mut app.input_state.pointers[hit.pointer];
    if pointer.now.scroll_x.abs() <= 0.1 && pointer.now.scroll_y.abs() <= 0.1 {
        return;
    }

    let config = &app.session.config;
    let scroll_factor = 90.0 * app.delta_time;

    let scroll_x = pointer.now.scroll_x
        * scroll_factor
        * config.scroll_speed
        * if config.invert_scroll_direction_x {
            -1.0
        } else {
            1.0
        };
    let scroll_y = pointer.now.scroll_y
        * scroll_factor
        * config.scroll_speed
        * if config.invert_scroll_direction_x {
            -1.0
        } else {
            1.0
        };

    if app.input_state.pointers[1 - hit.pointer]
        .interaction
        .grabbed
        .is_some_and(|x| x.grabbed_id == hit.overlay)
    {
        let can_curve = hovered
            .frame_meta()
            .is_some_and(|e| e.extent[0] >= e.extent[1]);

        // re-borrow
        let hovered_state = hovered.config.active_state.as_mut().unwrap();
        if can_curve {
            let cur = hovered_state.curvature.unwrap_or(0.0);
            let new = scroll_y.mul_add(-0.01, cur).min(0.5);
            if new <= f32::EPSILON {
                hovered_state.curvature = None;
            } else {
                hovered_state.curvature = Some(new);
            }
        } else {
            hovered_state.curvature = None;
        }
    } else {
        hovered.config.backend.on_scroll(
            app,
            hit,
            WheelDelta {
                x: scroll_x,
                y: scroll_y,
            },
        );
    }
}

fn get_nearest_hit<O>(
    pointer_idx: usize,
    overlays: &mut OverlayWindowManager<O>,
    app: &mut AppState,
) -> (Option<(PointerHit, RayHit)>, Option<Haptics>)
where
    O: Default,
{
    let pointer = &mut app.input_state.pointers[pointer_idx];
    if !pointer.tracked {
        return (None, None);
    }

    let ray_origin = pointer.pose;
    let mode = pointer.interaction.mode;
    let edit_mode = overlays.get_edit_mode();

    let mut hits: SmallVec<[RayHit; 8]> = smallvec!();

    let ids: Vec<_> = overlays.iter().map(|(id, _)| id).collect();
    for id in ids {
        let overlay = overlays.mut_by_id(id).unwrap();
        if pointer.touch_hit.is_some() {
            break;
        }
        if pointer.keyboard_projection_only && overlay.config.category != OverlayCategory::Keyboard
        {
            continue;
        }
        if app.watch_menu.open().is_some() && overlay.config.name.as_ref() != WATCH_NAME { continue; }
        if !overlay.config.room_available(&app.room) { continue; }
        let Some(overlay_state) = overlay.config.active_state.as_ref() else {
            continue;
        };
        // Use the opposite hand to operate the wrist panel.
        if pointer.hand_tracking
            && overlay.config.name.as_ref() == WATCH_NAME
            && matches!(overlay_state.positioning, Positioning::FollowHand { hand, .. } if hand as usize == pointer_idx)
        {
            continue;
        }
        if (!overlay_state.interactable || overlay_state.alpha < 0.025) && !edit_mode {
            continue;
        }

        let transform = if overlay.config.category == OverlayCategory::Keyboard {
            overlay_state.transform
                * overlay
                    .config
                    .backend
                    .frame_meta()
                    .map(|meta| meta.transform)
                    .unwrap_or_default()
        } else {
            overlay_state.transform
        };
        let hit = if overlay.config.category == OverlayCategory::Keyboard {
            keyboard_hit(
                pointer,
                id,
                &transform,
                overlay_state.curvature.as_ref(),
                overlay_state.grabbable,
                edit_mode,
            )
        } else {
            ray_test(
                &ray_origin,
                id,
                &transform,
                overlay_state.curvature.as_ref(),
            )
        };
        if let Some(hit) = hit
            && hit.dist.is_finite()
        {
            hits.push(hit);
        }
    }

    if let Some(hit) = pointer.touch_hit {
        hits.push(hit);
    }
    hits.sort_by(|a, b| a.dist.total_cmp(&b.dist));
    let hand_tracking = pointer.hand_tracking;
    let manipulating = pointer.now.grab || edit_mode;
    let keyboard_mode = keyboard_pointer_mode(pointer.now, pointer.before);

    for hit in hits {
        let overlay = overlays.mut_by_id(hit.overlay).unwrap(); // safe because we just got the id from the overlay

        let Some(uv) = overlay
            .config
            .backend
            .as_mut()
            .get_interaction_transform()
            .map(|a| a.transform_point2(hit.local_pos))
        else {
            continue;
        };

        if (uv.x < 0.0 || uv.x > 1.0 || uv.y < 0.0 || uv.y > 1.0) && !overlay.config.resizing {
            continue;
        }

        let mode = if hand_tracking && overlay.config.category == OverlayCategory::Keyboard {
            // Use explicit pinch gestures, independent of wrist orientation.
            keyboard_mode
        } else {
            mode
        };
        let pointer_hit = PointerHit {
            pointer: pointer_idx,
            overlay: hit.overlay,
            mode,
            primary: false,
            uv,
        };

        let result = overlay.config.backend.on_hover(app, &pointer_hit);
        let keyboard_grab = hand_tracking
            && manipulating
            && overlay.config.category == OverlayCategory::Keyboard
            && overlay
                .config
                .active_state
                .as_ref()
                .is_some_and(|s| s.grabbable);
        if result.consume || overlay.config.editing || keyboard_grab {
            return (Some((pointer_hit, hit)), result.haptics);
        }
    }

    (None, None)
}

struct StartGrabParams<'a> {
    idx: usize,
    id: OverlayID,
    name: Arc<str>,
    editing: bool,
    state: &'a mut OverlayWindowState,
    app: &'a mut AppState,
    edit_mode: bool,
    grab_float: bool,
}

fn start_grab(par: StartGrabParams) {
    let (app, id, state) = (par.app, par.id, par.state);
    if app.watch_menu.open().is_some() { return; }

    let pointer = &mut app.input_state.pointers[par.idx];

    // Grab anchor if:
    // - grabbed overlay is Anchored
    // - not in editmode
    // - not using grab_float
    // - grabbing with one hand. (grabbing with the 2nd hand will grab the individual overlay instead)
    let grab_anchor = !par.edit_mode
        && !par.grab_float
        && !app.anchor_grabbed
        && matches!(state.positioning, Positioning::Anchored)
        && !(app.session.config.middle_grabs_single
            && pointer.interaction.mode == PointerMode::Middle);

    let relative_grab_transform = if grab_anchor {
        app.anchor
    } else {
        state.transform
    };

    let offset = pointer.pose.inverse() * relative_grab_transform;

    app.anchor_grabbed = grab_anchor;

    pointer.interaction.grabbed = Some(GrabData {
        offset,
        grabbed_id: id,
        grab_anchor,
    });

    app.tasks.enqueue(TaskType::Overlay(OverlayTask::Modify(
        OverlaySelector::Id(id),
        Box::new({
            let pos = state.positioning;
            let name = par.name.clone();
            move |app, o| {
                let _ = o
                    .backend
                    .notify(
                        app,
                        OverlayEventData::OverlayGrabbed {
                            name,
                            pos,
                            editing: par.editing,
                        },
                    )
                    .inspect_err(|e| log::warn!("Error during Notify OverlayGrabbed: {e:?}"));
            }
        }),
    )));

    // Show anchor
    app.tasks.enqueue(TaskType::Overlay(OverlayTask::Modify(
        OverlaySelector::Name(ANCHOR_NAME.clone()),
        Box::new(|app, o| {
            o.activate(app, true);
        }),
    )));

    if let Some(hand) = pointer.hand()
        && !app.session.config.hide_grab_help
        && !pointer.handsfree
    {
        let pos = state.positioning;
        app.tasks.enqueue(TaskType::Overlay(OverlayTask::Modify(
            OverlaySelector::Name(GRAB_HELP_NAME.clone()),
            Box::new(move |app, o| {
                let _ = o
                    .backend
                    .notify(
                        app,
                        OverlayEventData::OverlayGrabbed {
                            name: par.name,
                            pos,
                            editing: par.editing,
                        },
                    )
                    .inspect_err(|e| log::warn!("Error during Notify OverlayGrabbed: {e:?}"));

                o.default_state.positioning = Positioning::FollowHand { hand, lerp: 0.1 };
                o.default_state.align_to_hmd = true;
                o.activate(app, true);
            }),
        )));
    }
}

fn handle_scale(transform: &mut Affine3A, scroll_y: f32) {
    let cur_scale = transform.x_axis.length();
    if cur_scale < 0.1 && scroll_y > 0.0 {
        return;
    }
    if cur_scale > 20. && scroll_y < 0.0 {
        return;
    }

    transform.matrix3 = transform
        .matrix3
        .mul_scalar(0.025f32.mul_add(-scroll_y, 1.0));
}

#[allow(clippy::too_many_lines)]
fn handle_grabbed<O>(idx: usize, overlay: &mut OverlayWindowData<O>, app: &mut AppState)
where
    O: Default,
{
    if !overlay.config.room_available(&app.room) {
        app.input_state.pointers[idx].interaction.grabbed = None;
        overlay.config.pause_movement = false;
        return;
    }
    let pointer = &mut app.input_state.pointers[idx];
    let Some(grab_data) = pointer.interaction.grabbed.as_mut() else {
        log::error!("Grabbed overlay does not exist");
        return;
    };
    let grab_anchor = grab_data.grab_anchor;

    if pointer.now.grab {
        let Some(overlay_state) = overlay.config.active_state.as_mut() else {
            // overlay got toggled off while being grabbed. those dastardly users!
            // just wait for them to release the grab
            return;
        };

        let scroll_factor = 90.0 * app.delta_time;

        if grab_anchor {
            if pointer.now.click {
                pointer.interaction.mode = PointerMode::Special;
                let grab_dist = grab_data.offset.translation.length().clamp(0.5, 5.0) * 0.2 + 0.4;
                handle_scale(
                    &mut app.anchor,
                    pointer.now.scroll_y * grab_dist * scroll_factor,
                );
                handle_scale(
                    &mut grab_data.offset,
                    pointer.now.scroll_y * grab_dist * scroll_factor,
                );
            } else if app.session.config.allow_sliding && pointer.now.scroll_y.is_finite() {
                // single grab push/pull
                let grab_dist = grab_data.offset.translation.length().clamp(0.5, 5.0);
                grab_data.offset.translation.z -=
                    pointer.now.scroll_y * scroll_factor * 0.02 * grab_dist;
                grab_data.offset.translation.z = grab_data.offset.translation.z.min(-0.05);
            }
            if pointer.now.click_modifier_right {
                app.anchor = pointer.pose * grab_data.offset;
            } else {
                app.anchor.translation =
                    pointer.pose.transform_point3a(grab_data.offset.translation);
                let scale = scalar_scale(&app.anchor);
                realign(
                    &mut app.anchor,
                    &app.input_state.hmd,
                    scale,
                    app.session.config.snap_angle_deg,
                );
            }
        } else {
            // single grab resize
            if pointer.now.click {
                pointer.interaction.mode = PointerMode::Special;
                let grab_dist = grab_data.offset.translation.length().clamp(0.5, 5.0) * 0.2 + 0.4;
                handle_scale(
                    &mut overlay_state.transform,
                    pointer.now.scroll_y * grab_dist * scroll_factor,
                );
                handle_scale(
                    &mut grab_data.offset,
                    pointer.now.scroll_y * grab_dist * scroll_factor,
                );
            } else if app.session.config.allow_sliding && pointer.now.scroll_y.is_finite() {
                // single grab push/pull
                let grab_dist = grab_data.offset.translation.length().clamp(0.5, 5.0);
                grab_data.offset.translation.z -=
                    pointer.now.scroll_y * scroll_factor * 0.02 * grab_dist;
                grab_data.offset.translation.z = grab_data.offset.translation.z.min(-0.05);
            }
            if pointer.now.click_modifier_right {
                overlay_state.transform = pointer.pose * grab_data.offset;
            } else {
                let scale = scalar_scale(&overlay_state.transform);
                overlay_state.transform.translation =
                    pointer.pose.transform_point3a(grab_data.offset.translation);
                realign(
                    &mut overlay_state.transform,
                    &app.input_state.hmd,
                    scale,
                    app.session.config.snap_angle_deg,
                );
            }
            overlay.config.pause_movement = true;
            overlay.config.dirty = true;
        }
    } else {
        // not now.grab
        pointer.interaction.grabbed = None;
        if grab_anchor {
            app.anchor_grabbed = false;
        } else {
            // single grab released
            if &*overlay.config.name == WATCH_NAME {
                // watch special: when dropped, follow the hand that wasn't grabbing
                if let Some(overlay_state) = overlay.config.active_state.as_mut() {
                    let align_to_hmd = overlay_state.align_to_hmd;
                    overlay_state.positioning = match overlay_state.positioning {
                        Positioning::FollowHand { hand, lerp } => match pointer.hand() {
                            Some(LeftRight::Left) => Positioning::FollowHand {
                                hand: LeftRight::Right,
                                lerp,
                            },
                            Some(LeftRight::Right) => Positioning::FollowHand {
                                hand: LeftRight::Left,
                                lerp,
                            },
                            _ => Positioning::FollowHand { hand, lerp },
                        },
                        x => x,
                    };
                    overlay_state.align_to_hmd = align_to_hmd;
                }
            } else if overlay.config.global
                && let Some(active_state) = overlay.config.active_state.as_ref()
            {
                let cur_scale = overlay.config.default_state.transform.x_axis.length();
                let tgt_scale = active_state.transform.x_axis.length();
                let mat = &mut overlay.config.default_state.transform.matrix3;
                *mat = mat.mul_scalar(tgt_scale / cur_scale);
            }
            overlay.config.pause_movement = false;
            if let Some(overlay_state) = overlay.config.active_state.as_mut() {
                window::save_transform(overlay_state, app);
            }
        }

        // Hide anchor
        app.tasks.enqueue(TaskType::Overlay(OverlayTask::Modify(
            OverlaySelector::Name(ANCHOR_NAME.clone()),
            Box::new(|_app, o| {
                o.deactivate();
            }),
        )));
        app.tasks.enqueue(TaskType::Overlay(OverlayTask::Modify(
            OverlaySelector::Name(GRAB_HELP_NAME.clone()),
            Box::new(|_app, o| {
                o.deactivate();
            }),
        )));
        log::debug!("Hand {}: dropped {}", idx, overlay.config.name);
    }
}

// Both hands manipulate one existing fist grab. Controller grabs and two
// different windows retain their ordinary, independent interaction paths.
fn hand_grip(pointer: &Pointer) -> bool {
    if pointer.domed_owned{return false;}
    pointer.hand_tracking && pointer.tracked && pointer.now.grab
        && !pointer.cancel_interaction && !pointer.joystick_owned
        && pointer.wrist_pose.is_some_and(|p| p.is_finite())
}
fn two_hand_target(pointers: &[Pointer; 2]) -> Option<OverlayID> {
    if !pointers.iter().all(hand_grip) { return None; }
    let left = pointers[0].interaction.grabbed;
    let right = pointers[1].interaction.grabbed;
    match (left, right) {
        (Some(a), Some(b)) if !a.grab_anchor && !b.grab_anchor && a.grabbed_id == b.grabbed_id => Some(a.grabbed_id),
        (Some(a), None) | (None, Some(a)) if !a.grab_anchor => Some(a.grabbed_id),
        _ => None,
    }
}
fn update_two_hand_grab<O: Default>(overlays: &mut OverlayWindowManager<O>, app: &mut AppState) -> bool {
    for pointer in &mut app.input_state.pointers {
        if pointer.hand_grab_needs_release {
            if !pointer.now.grab { pointer.hand_grab_needs_release = false; }
            pointer.now.grab = false;
        }
    }
    let mut interrupted = app.input_state.mouse_priority.active()
        || app.input_state.pointers.iter().any(|p| !p.tracked || !p.hand_tracking
            || p.cancel_interaction || p.joystick_owned || p.wrist_pose.is_none_or(|pose| !pose.is_finite()));
    if let Some((id, resize)) = app.input_state.two_hand_grab {
        let both = !interrupted && app.input_state.pointers.iter().all(hand_grip);
        let transform = overlays.mut_by_id(id).and_then(|o| {
            if !o.config.room_available(&app.room) { return None; }
            o.config.active_state.as_ref().map(|s| s.transform)
        });
        if both && transform.is_some() {
            let left = app.input_state.pointers[0].wrist_pose.unwrap().translation;
            let right = app.input_state.pointers[1].wrist_pose.unwrap().translation;
            if let Some(value) = resize.update(left, right) {
                let overlay = overlays.mut_by_id(id).unwrap();
                overlay.config.active_state.as_mut().unwrap().transform = value;
                overlay.config.pause_movement = true;
                overlay.config.dirty = true;
                return true;
            }
            interrupted = true;
        }
        // Rebase the surviving fist at the final resized transform. Tracking
        // loss/ownership changes cancel both instead and require opening them.
        app.input_state.two_hand_grab = None;
        let surviving = !interrupted && transform.is_some() && app.input_state.pointers.iter().any(hand_grip);
        let mut dropped = false;
        for idx in 0..2 {
            if !interrupted && transform.is_some() && hand_grip(&app.input_state.pointers[idx]) {
                let pointer = &mut app.input_state.pointers[idx];
                pointer.interaction.grabbed = Some(GrabData {
                    grabbed_id: id, grab_anchor: false, offset: pointer.pose.inverse() * transform.unwrap(),
                });
            } else {
                // A handoff is not a drop; only save/hide grab helpers once
                // when the last hand lets go.
                if surviving || dropped { app.input_state.pointers[idx].interaction.grabbed = None; }
                release_pointer(idx, overlays, app);
                dropped = true;
                if interrupted || transform.is_none() { app.input_state.pointers[idx].hand_grab_needs_release = true; }
            }
        }
        return false;
    }
    if interrupted { return false; }
    let Some(id) = two_hand_target(&app.input_state.pointers) else { return false; };
    let Some(transform) = overlays.mut_by_id(id).and_then(|o| {
            if !o.config.room_available(&app.room) { return None; }
            o.config.active_state.as_ref().map(|s| s.transform)
        }) else { return false; };
    let left = app.input_state.pointers[0].wrist_pose.unwrap().translation;
    let right = app.input_state.pointers[1].wrist_pose.unwrap().translation;
    let Some(resize) = crate::hand_resize::HandResize::start(transform, left, right) else {
        return true; // Freeze until the fists have a usable separation.
    };
    for idx in 0..2 {
        let now = app.input_state.pointers[idx].now;
        let before = app.input_state.pointers[idx].before;
        // Clear any click/hover ownership without ending the existing grab.
        app.input_state.pointers[idx].interaction.grabbed = None;
        release_pointer(idx, overlays, app);
        let pointer = &mut app.input_state.pointers[idx];
        pointer.now = now; pointer.before = before; pointer.tracked = true;
        pointer.interaction.grabbed = Some(GrabData {
            grabbed_id: id, grab_anchor: false, offset: pointer.pose.inverse() * transform,
        });
    }
    app.input_state.two_hand_grab = Some((id, resize));
    if let Some(overlay) = overlays.mut_by_id(id) { overlay.config.pause_movement = true; }
    true
}

fn ray_test(
    ray_origin: &Affine3A,
    overlay: OverlayID,
    overlay_pose: &Affine3A,
    curvature: Option<&f32>,
) -> Option<RayHit> {
    let (dist, local_pos) = raycast_overlay(ray_origin, overlay_pose, curvature.copied())?;

    if dist < 0.0 {
        // hit is behind us
        return None;
    }

    Some(RayHit {
        overlay,
        global_pos: ray_origin.transform_point3a(Vec3A::NEG_Z * dist),
        local_pos,
        dist,
    })
}

#[cfg(test)]
mod hand_attachment_tests {
    use super::*;
    #[test]
    fn resizing_requires_two_tracked_fists_and_one_target() {
        let mut ids = slotmap::SlotMap::<OverlayID, ()>::with_key();
        let first = ids.insert(());
        let second = ids.insert(());
        let mut pointers = [Pointer::new(0), Pointer::new(1)];
        for p in &mut pointers {
            p.tracked = true; p.hand_tracking = true; p.now.grab = true;
            p.wrist_pose = Some(Affine3A::IDENTITY);
        }
        assert!(two_hand_target(&pointers).is_none());
        pointers[0].interaction.grabbed = Some(GrabData { grabbed_id: first, ..Default::default() });
        assert_eq!(two_hand_target(&pointers), Some(first));
        pointers[1].interaction.grabbed = Some(GrabData { grabbed_id: second, ..Default::default() });
        assert!(two_hand_target(&pointers).is_none());
        pointers[1].interaction.grabbed = pointers[0].interaction.grabbed;
        assert_eq!(two_hand_target(&pointers), Some(first));
        pointers[1].tracked = false; assert!(two_hand_target(&pointers).is_none());
        pointers[1].tracked = true; pointers[1].hand_tracking = false;
        assert!(two_hand_target(&pointers).is_none());
        pointers[1].hand_tracking = true; pointers[1].cancel_interaction = true;
        assert!(two_hand_target(&pointers).is_none());
        pointers[1].cancel_interaction = false; pointers[1].joystick_owned = true;
        assert!(two_hand_target(&pointers).is_none());
        pointers[1].joystick_owned = false;
        pointers[0].interaction.grabbed.as_mut().unwrap().grab_anchor = true;
        assert!(two_hand_target(&pointers).is_none());
    }
    #[test]
    fn wrist_attachment_preserves_controller_frame_and_faces_back_of_hand() {
        let mut pointer = Pointer::new(0);
        pointer.pose = Affine3A::from_translation(glam::Vec3::X);
        assert_eq!(pointer.attachment_pose(), pointer.pose);
        let wrist = Affine3A::from_rotation_translation(
            Quat::from_rotation_y(0.7),
            glam::Vec3::new(0.2, 1.1, -0.4),
        );
        pointer.wrist_pose = Some(wrist);
        let panel = pointer.attachment_pose()
            * Affine3A::from_rotation_translation(
                crate::overlays::watch::WATCH_ROT,
                crate::overlays::watch::WATCH_POS,
            );
        let local = wrist.inverse() * panel;
        assert!(
            glam::Vec3::from(local.translation)
                .abs_diff_eq(glam::Vec3::new(0.0, 0.035, 0.025), 1e-5)
        );
        assert!(
            local
                .transform_vector3(glam::Vec3::X)
                .abs_diff_eq(glam::Vec3::NEG_Z, 1e-5)
        );
        assert!(
            local
                .transform_vector3(glam::Vec3::Z)
                .abs_diff_eq(glam::Vec3::Y, 1e-5)
        );
    }
    #[test]
    fn keyboard_contacts_select_between_three_fingers_and_ignore_the_laser() {
        let mut p = Pointer::new(0);
        p.hand_tracking = true;
        let tips = [Vec3A::new(-0.06, 0., 0.05), Vec3A::new(0.06, 0., 0.05), Vec3A::new(0., 0.09, 0.05)];
        let hit = keyboard_contacts(tips, OverlayID::default(), &Affine3A::IDENTITY, None).unwrap();
        assert!(hit.local_pos.abs_diff_eq(Vec2::new(0., 0.03), 1e-5));
        p.keyboard_contact = Some(hit);
        for rotation in [0., 1., 3.] {
            p.pose = Affine3A::from_rotation_translation(Quat::from_rotation_y(rotation), glam::Vec3::splat(10.));
            let selected = keyboard_hit(&p, hit.overlay, &Affine3A::IDENTITY, None, true, false).unwrap();
            assert_eq!(selected.local_pos, hit.local_pos);
        }
        // Each finger must be near, even if the average position is near.
        let mut far = tips;
        far[0].z = 0.4;
        assert!(keyboard_contacts(far, hit.overlay, &Affine3A::IDENTITY, None).is_none());
        far[0].z = f32::NAN;
        assert!(keyboard_contacts(far, hit.overlay, &Affine3A::IDENTITY, None).is_none());
    }

    #[test]
    fn keyboard_departure_releases_and_held_pinch_cannot_reenter() {
        let mut p = Pointer::new(0);
        p.hand_tracking = true;
        let hit = RayHit::default();
        // Entering pinched (including before debounce) never arms typing.
        update_keyboard_contact(&mut p, Some(hit));
        assert!(!keyboard_can_press(&p));
        p.keyboard_neutral = true;
        update_keyboard_contact(&mut p, Some(hit));
        assert!(keyboard_can_press(&p));
        p.now.click = true;
        p.keyboard_neutral = false;
        p.interaction.clicked_id = Some(hit.overlay);
        assert_eq!(update_keyboard_contact(&mut p, None).unwrap().overlay, hit.overlay);
        assert!(p.interaction.clicked_id.is_none());
        assert!(!keyboard_can_press(&p));
        update_keyboard_contact(&mut p, Some(hit));
        assert!(!keyboard_can_press(&p));
        p.now.click = false;
        p.before.click = true;
        p.keyboard_neutral = true;
        update_keyboard_contact(&mut p, Some(hit));
        assert!(!keyboard_can_press(&p));
        p.before.click = false;
        update_keyboard_contact(&mut p, Some(hit));
        assert!(keyboard_can_press(&p));
        p.now.grab = true;
        assert!(!keyboard_can_press(&p));
    }

    #[test]
    fn keyboard_edit_ray_does_not_authorize_typing_or_draw_a_typing_laser() {
        let mut p = Pointer::new(0);
        p.hand_tracking = true;
        p.pose = Affine3A::from_translation(glam::Vec3::Z);
        let hit = keyboard_hit(&p, OverlayID::default(), &Affine3A::IDENTITY, None, true, true).unwrap();
        assert!(!keyboard_can_press(&p));
        p.keyboard_contact = Some(hit);
        let mut lines = Vec::new();
        populate_lines(&mut lines, &p, Some((PointerHit::default(), hit)), &Affine3A::IDENTITY);
        assert!(lines.is_empty());
        p.now.grab = true;
        populate_lines(&mut lines, &p, Some((PointerHit::default(), hit)), &Affine3A::IDENTITY);
        assert_eq!(lines.len(), 1);
    }

    #[test]
    fn keyboard_middle_pinch_types_and_index_pinch_deletes_on_press_and_release() {
        for (pinch, mode) in [
            (
                PointerState {
                    click: true,
                    click_modifier_middle: true,
                    ..Default::default()
                },
                PointerMode::Left,
            ),
            (
                PointerState {
                    click: true,
                    ..Default::default()
                },
                PointerMode::Middle,
            ),
            (
                PointerState {
                    click: true,
                    click_modifier_right: true,
                    ..Default::default()
                },
                PointerMode::Right,
            ),
        ] {
            assert_eq!(keyboard_pointer_mode(pinch, PointerState::default()), mode);
            assert_eq!(keyboard_pointer_mode(PointerState::default(), pinch), mode);
        }
    }

    #[test]
    fn keyboard_fist_target_survives_curling_but_expires_after_debounce() {
        let mut ids = slotmap::SlotMap::<OverlayID, ()>::with_key();
        let id = ids.insert(());
        let mut pointer = Pointer::new(0);
        let now = Instant::now();
        assert!(keyboard_grab_candidate(&pointer, now).is_none());
        pointer.keyboard_grab_target = Some((
            RayHit {
                overlay: id,
                dist: 0.2,
                ..Default::default()
            },
            now,
        ));
        // The selecting index tip can leave the keyboard while the fist closes.
        pointer.index_tip = Some(Vec3A::new(2., -1., 0.));
        let hit = keyboard_grab_candidate(&pointer, now + Duration::from_millis(100)).unwrap();
        assert_eq!(hit.overlay, id);
        assert_eq!(hit.dist, 0.);
        assert!(keyboard_grab_candidate(&pointer, now + Duration::from_millis(251)).is_none());
    }

    #[test]
    fn keyboard_grab_and_edit_use_normal_ui_rays_when_the_index_is_curled() {
        let mut ids = slotmap::SlotMap::<OverlayID, ()>::with_key();
        let id = ids.insert(());
        let other = ids.insert(());
        let mut pointer = Pointer::new(0);
        pointer.hand_tracking = true;
        pointer.index_tip = Some(Vec3A::new(2., -1., 1.));
        pointer.pose = Affine3A::from_translation(glam::Vec3::Z);
        assert!(keyboard_hit(&pointer, id, &Affine3A::IDENTITY, None, true, false).is_none());
        pointer.now.grab = true;
        assert!(
            keyboard_hit(&pointer, id, &Affine3A::IDENTITY, None, true, false)
                .unwrap()
                .local_pos
                .abs_diff_eq(Vec2::ZERO, 1e-5)
        );
        pointer.pose = Affine3A::from_rotation_translation(
            Quat::from_rotation_y(std::f32::consts::PI),
            glam::Vec3::Z,
        );
        pointer.keyboard_grab_target = Some((
            RayHit {
                overlay: id,
                ..Default::default()
            },
            Instant::now(),
        ));
        assert!(keyboard_hit(&pointer, id, &Affine3A::IDENTITY, None, true, false).is_some());
        assert!(keyboard_hit(&pointer, other, &Affine3A::IDENTITY, None, true, false).is_none());
        pointer.keyboard_grab_target = None;
        pointer.now.grab = false;
        pointer.pose = Affine3A::from_translation(glam::Vec3::Z);
        assert!(keyboard_hit(&pointer, id, &Affine3A::IDENTITY, None, true, true).is_some());
    }

    #[test]
    fn keyboard_hover_accepts_depth_without_pressing_and_rejects_distant_hands() {
        let transform = Affine3A::from_scale_rotation_translation(
            glam::Vec3::splat(0.65),
            Quat::from_rotation_x(-0.3),
            glam::Vec3::new(0., 1., -0.5),
        );
        let surface = transform.transform_point3a(Vec3A::new(0.2, -0.1, 0.));
        let normal = transform.z_axis.normalize();
        for depth in [-0.04, 0., 0.15] {
            let hit = keyboard_projection(
                surface + normal * depth,
                OverlayID::default(),
                &transform,
                None,
            )
            .unwrap();
            assert!(hit.local_pos.abs_diff_eq(Vec2::new(0.2, -0.1), 1e-5));
        }
        assert!(
            keyboard_projection(
                surface + normal * 0.4,
                OverlayID::default(),
                &transform,
                None
            )
            .is_none()
        );
    }

    #[test]
    fn touch_projection_uses_surface_coordinates_after_rotation_and_scaling() {
        let transform = Affine3A::from_scale_rotation_translation(
            glam::Vec3::splat(0.7),
            Quat::from_rotation_y(0.5),
            glam::Vec3::new(0.1, 1.0, -0.7),
        );
        let on_surface = transform.transform_point3a(Vec3A::new(0.2, -0.1, 0.0));
        let normal = transform.z_axis.normalize();
        let (hit, d) = touch_projection(
            on_surface + normal * 0.003,
            OverlayID::default(),
            &transform,
            None,
        )
        .unwrap();
        assert!((d - 0.003).abs() < 1e-5);
        assert!(hit.local_pos.abs_diff_eq(Vec2::new(0.2, -0.1), 1e-5));
        assert!(hit.global_pos.abs_diff_eq(on_surface, 1e-5));
        assert!(
            touch_projection(
                on_surface + normal * 0.10,
                OverlayID::default(),
                &transform,
                None
            )
            .is_none()
        );
        assert!(
            touch_projection(
                on_surface - normal * 0.04,
                OverlayID::default(),
                &transform,
                None
            )
            .is_none()
        );
    }
}

fn route_domed<O:Default>(overlays:&mut OverlayWindowManager<O>,app:&mut AppState) {
    let enabled=app.domed.active()&&app.watch_menu.open().is_none()&&!app.hands_frame.model.reserving();
    let mut nearest=[100f32;2];
    for i in 0..2 {
        let pointer=&app.input_state.pointers[i];
        for (_,overlay) in overlays.iter(){
            if !overlay.config.room_available(&app.room){continue;}
            let Some(state)=overlay.config.active_state.as_ref().filter(|s|s.interactable&&s.alpha>=0.025) else{continue;};
            if pointer.hand_tracking&&overlay.config.name.as_ref()==WATCH_NAME&&matches!(state.positioning,Positioning::FollowHand{hand,..} if hand as usize==i){continue;}
            if let Some((distance,_))=raycast_overlay(&pointer.pose,&state.transform,state.curvature){if distance>=0.{nearest[i]=nearest[i].min(distance);}}
        }
    }
    let head_inverse=app.input_state.hmd.inverse();
    let rays=std::array::from_fn(|i|{
        let p=&app.input_state.pointers[i];
        let valid=enabled&&p.tracked&&!p.joystick_owned&&!p.now.space_drag&&!p.cancel_interaction&&p.touch_hit.is_none()&&!p.keyboard_hover&&p.interaction.grabbed.is_none()&&!(p.hand_tracking&&app.input_state.mouse_priority.active());
        let button=match p.interaction.mode{PointerMode::Middle=>2,PointerMode::Right=>4,_=>1};
        crate::domed_link::Ray{tracked:valid,origin:head_inverse.transform_point3a(p.pose.translation).to_array(),direction:head_inverse.transform_vector3a(p.pose.transform_vector3a(Vec3A::NEG_Z)).normalize_or_zero().to_array(),buttons:if p.now.click{button}else{0},scroll:[(p.now.scroll_x as f64).clamp(-20.,20.),(p.now.scroll_y as f64).clamp(-20.,20.)],max_distance:nearest[i]}
    });
    app.domed.pointers(rays.clone());
    for i in 0..2 {
        let owned=rays[i].tracked&&app.domed.hit(i).is_some_and(|d|d<nearest[i]);
        let old=app.input_state.pointers[i].domed_owned;
        if owned&&!old {
            let now=app.input_state.pointers[i].now;let before=app.input_state.pointers[i].before;let tracked=app.input_state.pointers[i].tracked;
            release_pointer(i,overlays,app);
            let p=&mut app.input_state.pointers[i];p.now=now;p.before=before;p.tracked=tracked;
        }
        if old&&!owned {app.input_state.pointers[i].domed_release_gate=true;}
        app.input_state.pointers[i].domed_owned=owned;
    }
}
