use glam::{Affine3A, Mat3A, Quat, Vec3, Vec3A, vec3a};
use libmonado::{Monado, Pose, ReferenceSpaceType};
use wgui::log::LogErr;

use crate::{
    backend::{
        playspace_common::{self, SpaceGravity, SpaceGravityUpdateParams},
        task::PlayspaceTask,
    },
    state::{AppState, PlayspaceState, load_playspace_state, save_playspace_state},
    windowing::manager::OverlayWindowManager,
};

use super::overlay::OpenXrOverlayData;

struct MoverData<T> {
    pose: Affine3A,
    hand: usize,
    hand_pose: T,
}

pub(super) struct PlayspaceMover {
    drag: Option<MoverData<Vec3A>>,
    rotate: Option<MoverData<Quat>>,
    gravity: SpaceGravity,
    playspace_state: PlayspaceState,
    floor_applied_revision: Option<u64>,
    floor_retry_at: std::time::Instant,
}

impl PlayspaceMover {
    pub fn new() -> Self {
        log::info!("Monado: using space offset API");

        Self {
            drag: None,
            rotate: None,
            gravity: SpaceGravity::new(),
            playspace_state: load_playspace_state().unwrap_or_default(),
            floor_applied_revision: None,
            floor_retry_at: std::time::Instant::now(),
        }
    }

    pub fn handle_task(
        &mut self,
        app: &mut AppState,
        overlays: &mut OverlayWindowManager<OpenXrOverlayData>,
        task: PlayspaceTask,
    ) {
        match task {
            PlayspaceTask::RememberFloor => { self.remember_floor(app); return; }
            PlayspaceTask::ForgetFloor => { self.forget_floor(app); return; }
            _ => {}
        }
        let Some(monado) = &mut app.monado_state else {
            return; // monado not available
        };

        match task {
            PlayspaceTask::RememberFloor | PlayspaceTask::ForgetFloor => unreachable!(),
            PlayspaceTask::FixFloor => {
                self.fix_floor(app, overlays);
            }
            PlayspaceTask::Reset => {
                self.reset_offset(app, overlays);
            }
            PlayspaceTask::Recenter => {
                self.recenter(app, overlays);
            }
            PlayspaceTask::SaveCenter => {
                self.save_center(&mut monado.ipc);
            }
            PlayspaceTask::ResetCenter => {
                self.playspace_state.openxr_space_center = Affine3A::IDENTITY;
            }
        }
    }

    fn moving(&self, app: &AppState) -> bool {
        self.drag.is_some() || self.rotate.is_some()
            || app.input_state.pointers.iter().any(|p| p.now.space_drag || p.now.space_rotate || p.now.space_reset)
    }

    fn remember_floor(&mut self, app: &mut AppState) {
        if app.monado_state.is_none() || self.moving(app) {
            app.room_notice("Remember floor requires WiVRn OpenXR with playspace movement stopped.");
            return;
        }
        let level = app.monado_state.as_mut().and_then(|m|
            m.ipc.get_reference_space_offset(ReferenceSpaceType::Stage).ok()
        ).is_some_and(|pose| {
            let rotation: Quat = pose.orientation.into();
            (rotation * Vec3::Y).dot(Vec3::Y) > 5.0f32.to_radians().cos()
        });
        if !level {
            app.room_notice("Floor not saved. Return the playspace to an upright orientation first.");
            return;
        }
        let Some(binding) = app.room.remember_floor() else {
            app.room_notice("Floor not saved. Localize a saved room marker and hold steady for two seconds.");
            return;
        };
        let mut candidate = self.playspace_state.clone();
        candidate.remembered_floor = Some(binding);
        match save_playspace_state(&candidate) {
            Ok(()) => {
                self.playspace_state = candidate;
                self.floor_applied_revision = Some(app.room.revision());
                app.room_notice("Floor remembered. It will restore after reconnecting or a reference-space change.");
            }
            Err(e) => app.room_notice(&format!("Could not save floor: {e}")),
        }
    }

    fn forget_floor(&mut self, app: &mut AppState) {
        let mut candidate = self.playspace_state.clone();
        candidate.remembered_floor = None;
        match save_playspace_state(&candidate) {
            Ok(()) => {
                self.playspace_state = candidate;
                self.floor_applied_revision = None;
                app.room_notice("Remembered floor removed. Automatic floor restoration is off.");
            }
            Err(e) => app.room_notice(&format!("Could not remove saved floor: {e}")),
        }
    }

    /// Apply once per connection/reference generation. An already applied floor
    /// never fights deliberate playspace dragging or accumulates corrections.
    pub fn restore_floor(
        &mut self,
        app: &mut AppState,
        overlays: &mut OverlayWindowManager<OpenXrOverlayData>,
    ) {
        let revision = app.room.revision();
        app.room.floor_status = if self.playspace_state.remembered_floor.is_none() {
            "Remembered floor: off"
        } else if self.floor_applied_revision == Some(revision) {
            "Remembered floor: active"
        } else {
            "Remembered floor: waiting for stable room localization"
        };
        if self.floor_applied_revision == Some(revision) || self.moving(app)
            || std::time::Instant::now() < self.floor_retry_at { return; }
        let Some(binding) = &self.playspace_state.remembered_floor else { return; };
        let Some(world) = app.room.locate_floor(binding) else { return; };
        let Some(monado) = &mut app.monado_state else { return; };
        self.floor_retry_at = std::time::Instant::now() + std::time::Duration::from_secs(1);
        let Ok(pose) = monado.ipc.get_reference_space_offset(ReferenceSpaceType::Stage) else { return; };
        let before = Affine3A::from_rotation_translation(pose.orientation.into(), pose.position.into());
        let Some(after) = crate::room::floor_offset(before, world) else { return; };
        let new_pose = Pose { position: after.translation.into(), orientation: pose.orientation };
        if let Err(e) = monado.ipc.set_reference_space_offset(ReferenceSpaceType::Stage, new_pose) {
            log::warn!("Could not restore room floor: {e:?}");
            return;
        }
        self.floor_applied_revision = Some(revision);
        self.gravity.reset();
        self.playspace_state.openxr_space_center.translation += after.translation - before.translation;
        if !app.session.config.space_drag_affects_world {
            playspace_common::shift_world(overlays, &mut app.anchor, &before, &after);
        }
        app.room.stage_changed();
        app.room_notice("Remembered room floor restored.");
    }

    pub fn update(
        &mut self,
        overlays: &mut OverlayWindowManager<OpenXrOverlayData>,
        app: &mut AppState,
    ) {
        let Some(monado) = &mut app.monado_state else {
            return; // monado not available
        };

        for pointer in &app.input_state.pointers {
            if pointer.now.space_reset {
                if !pointer.before.space_reset {
                    log::info!("Space reset");
                    self.reset_offset(app, overlays);
                }
                return;
            }
        }

        if let Some(mut data) = self.rotate.take() {
            let pointer = &app.input_state.pointers[data.hand];
            if !pointer.now.space_rotate {
                log::info!("End space rotate");
                return;
            }

            let pose_quat = Quat::from_affine3(&data.pose).normalize();
            let new_hand = (pose_quat
                * Quat::from_affine3(&app.input_state.pointers[data.hand].raw_pose))
            .normalize();

            let dq = (pose_quat.conjugate() * (new_hand * data.hand_pose.conjugate()) * pose_quat)
                .normalize();
            let mut space_transform = if app.session.config.space_rotate_unlocked {
                Affine3A::from_quat(dq)
            } else {
                let rel_y = f32::atan2(
                    2.0 * dq.y.mul_add(dq.w, dq.x * dq.z),
                    2.0f32.mul_add(dq.w.mul_add(dq.w, dq.x * dq.x), -1.0),
                );

                Affine3A::from_rotation_y(rel_y)
            };
            let offset = (space_transform.transform_vector3a(app.input_state.hmd.translation)
                - app.input_state.hmd.translation)
                * -1.0;

            space_transform.translation = offset;

            let before_pose = data.pose;
            data.pose *= space_transform;
            data.pose.matrix3 = Mat3A::from_quat(Quat::from_affine3(&data.pose).normalize());
            if !app.session.config.space_drag_affects_world {
                playspace_common::shift_world(overlays, &mut app.anchor, &before_pose, &data.pose);
            }
            data.hand_pose = new_hand;

            apply_offset(data.pose, &mut monado.ipc);
            app.room.stage_changed();
            self.rotate = Some(data);
        } else {
            // start space rotate
            for (i, pointer) in app.input_state.pointers.iter().enumerate() {
                if pointer.now.space_rotate {
                    let Ok(pose) = monado
                        .ipc
                        .get_reference_space_offset(ReferenceSpaceType::Stage)
                        .log_err("Could not get reference space offset.")
                        .map(|p| {
                            Affine3A::from_rotation_translation(
                                p.orientation.into(),
                                p.position.into(),
                            )
                        })
                    else {
                        return;
                    };

                    let hand_pose = Quat::from_affine3(&(pose * pointer.raw_pose)).normalize();
                    self.rotate = Some(MoverData {
                        pose,
                        hand: i,
                        hand_pose,
                    });
                    self.drag = None;
                    log::info!("Start space rotate");
                    return;
                }
            }
        }

        if let Some(mut data) = self.drag.take() {
            let new_hand = data
                .pose
                .transform_point3a(app.input_state.pointers[data.hand].raw_pose.translation);
            let relative_pos = if app.session.config.space_drag_unlocked {
                new_hand - data.hand_pose
            } else {
                vec3a(0., new_hand.y - data.hand_pose.y, 0.)
            } * app.session.config.space_drag_multiplier;
            let pointer = &app.input_state.pointers[data.hand];

            if !pointer.now.space_drag {
                self.gravity.mark_end_drag(
                    &app.session.config,
                    relative_pos,
                    data.pose.translation,
                    app.delta_time,
                );

                log::info!("End space drag");
                return;
            }

            if relative_pos.length_squared() > 1000.0 {
                log::warn!("Space drag too fast, ignoring");
                return;
            }

            let before_pose = data.pose;
            data.pose.translation += relative_pos;
            if !app.session.config.space_drag_affects_world {
                playspace_common::shift_world(overlays, &mut app.anchor, &before_pose, &data.pose);
            }
            data.hand_pose = new_hand;

            apply_offset(data.pose, &mut monado.ipc);
            app.room.stage_changed();
            self.drag = Some(data);
        } else {
            // start space drag
            for (i, pointer) in app.input_state.pointers.iter().enumerate() {
                if pointer.now.space_drag {
                    let Ok(pose) = monado
                        .ipc
                        .get_reference_space_offset(ReferenceSpaceType::Stage)
                        .log_err("Could not get reference space offset.")
                        .map(|p| {
                            Affine3A::from_rotation_translation(
                                p.orientation.into(),
                                p.position.into(),
                            )
                        })
                    else {
                        return;
                    };

                    let hand_pos = pose.transform_point3a(pointer.raw_pose.translation);
                    self.drag = Some(MoverData {
                        pose,
                        hand: i,
                        hand_pose: hand_pos,
                    });
                    log::info!("Start space drag");
                    return;
                }
            }
        }

        if let Some(res) = self.gravity.update(SpaceGravityUpdateParams {
            dt: app.delta_time,
            dragging: self.drag.is_some(),
            config: &app.session.config,
            floor_height: app.session.config.space_gravity_floor_height,
        }) {
            let prev_playspace_pos = res.playspace_pos - res.playspace_pos_offset;
            let before_pose = Affine3A::from_translation(prev_playspace_pos.into());
            let after_pose = Affine3A::from_translation(res.playspace_pos.into());
            apply_offset(after_pose, &mut monado.ipc);
            app.room.stage_changed();

            if !app.session.config.space_drag_affects_world {
                playspace_common::shift_world(overlays, &mut app.anchor, &before_pose, &after_pose);
            }
        }
    }

    pub fn recenter(
        &mut self,
        app: &mut AppState,
        overlays: &mut OverlayWindowManager<OpenXrOverlayData>,
    ) {
        let Some(monado) = &mut app.monado_state else {
            return;
        };

        if self.drag.is_some() {
            log::info!("Space drag interrupted by recenter");
            self.drag = None;
        }
        if self.rotate.is_some() {
            log::info!("Space rotate interrupted by recenter");
            self.rotate = None;
        }

        let input = &app.input_state;
        let anchor = &mut app.anchor;

        let Ok(mut pose) = monado
            .ipc
            .get_reference_space_offset(ReferenceSpaceType::Stage)
            .inspect_err(|e| log::warn!("Could not recenter due to libmonado error: {e:?}"))
        else {
            return;
        };

        let before =
            Affine3A::from_rotation_translation(pose.orientation.into(), pose.position.into());

        let cur_rot: Quat = pose.orientation.into();
        let cur_pos: Vec3 = pose.position.into();

        let mut stage_offset = Affine3A::from_rotation_translation(cur_rot, cur_pos);

        let horiz_hmd_pos = Vec3::new(input.hmd.translation.x, 0.0, input.hmd.translation.z);

        let fwd = input.hmd.transform_vector3a(Vec3A::NEG_Z);
        let horiz_len_sq = fwd.x.mul_add(fwd.x, fwd.z * fwd.z);

        let hmd_yaw = if horiz_len_sq > f32::EPSILON {
            let yaw = (-fwd.x).atan2(-fwd.z);
            Quat::from_rotation_y(yaw)
        } else {
            Quat::IDENTITY
        };

        let recenter_offset = Affine3A::from_rotation_translation(hmd_yaw, horiz_hmd_pos);

        stage_offset *= recenter_offset;

        let (_, new_rot, new_pos) = stage_offset.to_scale_rotation_translation();

        pose.position = new_pos.into();

        if horiz_len_sq > f32::EPSILON {
            pose.orientation = new_rot.into();
        }

        let _ = monado
            .ipc
            .set_reference_space_offset(ReferenceSpaceType::Stage, pose)
            .inspect_err(|e| log::warn!("Could not recenter due to libmonado error: {e:?}"));
        app.room.stage_changed();

        self.gravity.reset();

        if !app.session.config.space_drag_affects_world {
            let after =
                Affine3A::from_rotation_translation(pose.orientation.into(), pose.position.into());
            playspace_common::shift_world(overlays, anchor, &before, &after);
        }
    }

    pub fn reset_offset(
        &mut self,
        app: &mut AppState,
        overlays: &mut OverlayWindowManager<OpenXrOverlayData>,
    ) {
        let Some(monado) = &mut app.monado_state else {
            return;
        };

        if self.drag.is_some() {
            log::info!("Space drag interrupted by manual reset");
            self.drag = None;
        }
        if self.rotate.is_some() {
            log::info!("Space rotate interrupted by manual reset");
            self.rotate = None;
        }

        let Ok(pose) = monado
            .ipc
            .get_reference_space_offset(ReferenceSpaceType::Stage)
            .inspect_err(|e| log::warn!("Could not reset offset due to libmonado error: {e:?}"))
        else {
            return;
        };

        let before =
            Affine3A::from_rotation_translation(pose.orientation.into(), pose.position.into());

        self.gravity.reset();
        let offset = self.playspace_state.openxr_space_center;
        apply_offset(offset, &mut monado.ipc);
        app.room.stage_changed();

        if !app.session.config.space_drag_affects_world {
            let after = offset;
            playspace_common::shift_world(overlays, &mut app.anchor, &before, &after);
        }
    }

    pub fn fix_floor(
        &mut self,
        app: &mut AppState,
        overlays: &mut OverlayWindowManager<OpenXrOverlayData>,
    ) {
        let Some(monado) = &mut app.monado_state else {
            return;
        };

        if self.drag.is_some() {
            log::info!("Space drag interrupted by fix floor");
            self.drag = None;
        }
        if self.rotate.is_some() {
            log::info!("Space rotate interrupted by fix floor");
            self.rotate = None;
        }

        let input = &app.input_state;
        let anchor = &mut app.anchor;

        let Ok(mut pose) = monado
            .ipc
            .get_reference_space_offset(ReferenceSpaceType::Stage)
            .inspect_err(|e| log::warn!("Could not fix floor due to libmonado error: {e:?}"))
        else {
            return;
        };

        let before =
            Affine3A::from_rotation_translation(pose.orientation.into(), pose.position.into());

        let y1 = input.pointers[0].raw_pose.translation.y;
        let y2 = input.pointers[1].raw_pose.translation.y;
        let delta = y1.min(y2) - 0.05;

        pose.position.y += delta;

        self.playspace_state.openxr_space_center.translation.y = pose.position.y;

        let _ = monado
            .ipc
            .set_reference_space_offset(ReferenceSpaceType::Stage, pose)
            .inspect_err(|e| log::warn!("Could not fix floor due to libmonado error: {e:?}"));
        app.room.stage_changed();

        let after =
            Affine3A::from_rotation_translation(pose.orientation.into(), pose.position.into());

        if !app.session.config.space_drag_affects_world {
            playspace_common::shift_world(overlays, anchor, &before, &after);
        }
    }

    pub fn save_center(&mut self, monado: &mut Monado) {
        if self.drag.is_some() {
            log::info!("Space drag interrupted by save center");
            self.drag = None;
        }
        if self.rotate.is_some() {
            log::info!("Space rotate interrupted by save center");
            self.rotate = None;
        }

        let Ok(pose) = monado
            .get_reference_space_offset(ReferenceSpaceType::Stage)
            .inspect_err(|e| log::warn!("Could not fix floor due to libmonado error: {e:?}"))
        else {
            return;
        };

        let cur_rot: Quat = pose.orientation.into();
        let cur_pos: Vec3 = pose.position.into();

        let stage_offset = Affine3A::from_rotation_translation(cur_rot, cur_pos);
        self.playspace_state.openxr_space_center = stage_offset;
        let _ =
            save_playspace_state(&self.playspace_state).log_err("Could not save playspace state");
    }
}

fn apply_offset(transform: Affine3A, monado: &mut Monado) {
    let pose = Pose {
        position: transform.translation.into(),
        orientation: Quat::from_affine3(&transform).into(),
    };
    let _ = monado.set_reference_space_offset(ReferenceSpaceType::Stage, pose);
}
