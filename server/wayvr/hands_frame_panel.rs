// SPDX-License-Identifier: GPL-3.0-only
use crate::{
    backend::task::{OverlayTask, SpawnPos, TaskType},
    gui::{
        panel::{GuiPanel, NewGuiPanelParams},
        timer::GuiTimer,
    },
    hands_frame::{Hand, Rect},
    state::AppState,
    windowing::{
        OverlaySelector, Z_ORDER_HELP, manager::OverlayWindowManager, window::OverlayWindowConfig,
    },
};
use glam::{Affine3A, Quat, Vec3, Vec3A};
use std::{rc::Rc, time::Duration};
use wgui::{
    components::button::ComponentButton,
    event::EventListenerKind,
    i18n::Translation,
    parser::Fetchable,
    renderer_vk::text::custom_glyph::CustomGlyphData,
    widget::{EventResult, image::WidgetImage, label::WidgetLabel},
};
use wlx_common::windowing::{OverlayWindowState, Positioning};
pub const PANEL: &str = "hands-frame";
pub const GUIDE: &str = "hands-frame-guide";
pub const BOOKMARKS: &str = "hands-frame-bookmarks";
pub fn toggle(app: &mut AppState) {
    app.hands_frame.enabled = !app.hands_frame.enabled;
    app.hands_frame.model.cancel();
    app.hands_frame.start();
    for name in [PANEL, GUIDE] {
        app.tasks
            .enqueue(TaskType::Overlay(OverlayTask::Drop(OverlaySelector::Name(
                name.into(),
            ))));
        if app.hands_frame.enabled {
            app.tasks.enqueue(TaskType::Overlay(OverlayTask::Spawn(
                OverlaySelector::Name(name.into()),
                SpawnPos::FixedNoRealign,
                Box::new(move |app| {
                    create(app, name == GUIDE)
                        .inspect_err(|e| log::warn!("Hands Frame panel: {e:#}"))
                        .ok()
                }),
            )));
        }
    }
    app.tasks
        .enqueue(TaskType::Overlay(OverlayTask::Drop(OverlaySelector::Name(
            BOOKMARKS.into(),
        ))));
    app.tasks.enqueue(TaskType::Overlay(OverlayTask::Spawn(
        OverlaySelector::Name(BOOKMARKS.into()),
        SpawnPos::FixedNoRealign,
        Box::new(|app| {
            create_bookmarks(app)
                .inspect_err(|e| log::warn!("Hands Frame bookmarks: {e:#}"))
                .ok()
        }),
    )));
    if !app.hands_frame.enabled {
        app.hands_frame.command("cancel");
    }
}
fn create(app: &mut AppState, guide: bool) -> anyhow::Result<OverlayWindowConfig> {
    let mut panel = GuiPanel::new_from_template(
        app,
        if guide {
            "gui/hands-frame-guide.xml"
        } else {
            "gui/hands-frame.xml"
        },
        (),
        NewGuiPanelParams::default(),
    )?;
    let image = panel.parser_state.get_widget_id("preview")?;
    let label = panel.parser_state.get_widget_id("status")?;
    let revision = std::cell::Cell::new(u64::MAX);
    let previous_svg = std::cell::RefCell::new(String::new());
    panel.add_event_listener(image,EventListenerKind::InternalStateChange,Box::new(move|common,data,app,_|{
        let image=data.obj.get_as_mut::<WidgetImage>().unwrap();
        if guide {
            let rect=app.hands_frame.model.rect;let aspect=rect.map_or(1.,|r|(r.bounds[2]-r.bounds[0])/(r.bounds[3]-r.bounds[1]));
            let aspect=(aspect.clamp(0.1,10.)*32.).round()/32.;
            let (w,h)=if aspect>=1. {(980.,980./aspect)}else{(980.*aspect,980.)};
            let color=if app.hands_frame.model.hide_guide{"none"}else{"#35d9d1"};
            let svg=format!("<svg xmlns=\"http://www.w3.org/2000/svg\" viewBox=\"0 0 1000 1000\"><rect x=\"{}\" y=\"{}\" width=\"{w}\" height=\"{h}\" rx=\"6\" fill=\"none\" stroke=\"{color}\" stroke-width=\"6\"/></svg>",(1000.-w)/2.,(1000.-h)/2.);
            if *previous_svg.borrow()!=svg {image.set_content(common.alterables,Some(CustomGlyphData::from_bytes_svg(&app.wgui_globals,"hands-frame-guide",svg.as_bytes())?));*previous_svg.borrow_mut()=svg;}
        }else{
            let status=app.hands_frame.status.lock().unwrap().clone();
            if revision.get()!=status.revision {revision.set(status.revision);image.set_content(common.alterables,status.png.as_ref().and_then(|b|CustomGlyphData::from_bytes_raster(&app.wgui_globals,&status.preview,b).ok()));}
        }Ok(EventResult::Pass)
    }));
    panel.add_event_listener(
        label,
        EventListenerKind::InternalStateChange,
        Box::new(move |common, data, app, _| {
            let text = if guide {
                app.hands_frame.model.caption.clone()
            } else {
                let status = app.hands_frame.status.lock().unwrap();
                format!(
                    "{}\n{}\n{} · {}{}\n{}",
                    status.source,
                    status.status,
                    status.language,
                    status.page,
                    if status.bookmarked {
                        " · bookmarked"
                    } else {
                        ""
                    },
                    status.text
                )
            };
            data.obj
                .get_as_mut::<WidgetLabel>()
                .unwrap()
                .set_text(common, Translation::from_raw_text(&text));
            Ok(EventResult::Pass)
        }),
    );
    if !guide {
        for action in [
            "save",
            "copy",
            "bookmark",
            "bookmarks",
            "previous",
            "next",
            "original",
            "open",
            "close",
            "auto_scan",
            "language",
            "source",
            "mirror_fov",
        ] {
            let button = panel
                .parser_state
                .fetch_component_as::<ComponentButton>(action)?;
            let sender = app.hands_frame.sender();
            button.on_click(Rc::new(move |_, _| {
                if let Some(tx) = &sender {
                    let _ = tx.try_send(serde_json::json!({"op":action}));
                }
                Ok(())
            }));
        }
    }
    panel
        .timers
        .push(GuiTimer::new(Duration::from_millis(100), 0));
    panel.update_layout(app)?;
    Ok(OverlayWindowConfig {
        name: if guide { GUIDE } else { PANEL }.into(),
        global: true,
        z_order: Z_ORDER_HELP,
        show_on_spawn: true,
        default_state: OverlayWindowState {
            interactable: !guide,
            grabbable: !guide,
            block_input: !guide,
            positioning: if guide {
                Positioning::Static
            } else {
                Positioning::Floating
            },
            transform: Affine3A::from_scale_rotation_translation(
                Vec3::splat(if guide { 0.001 } else { 0.7 }),
                Quat::IDENTITY,
                Vec3::new(0.35, 0., -1.),
            ),
            ..Default::default()
        },
        ..OverlayWindowConfig::from_backend(Box::new(panel))
    })
}
pub fn update<O: Default>(overlays: &mut OverlayWindowManager<O>, app: &mut AppState) -> bool {
    if !app.input_state.pointers[1].hand_tracking {
        app.hands_frame
            .model
            .adjust_zoom(app.input_state.pointers[1].now.scroll_y * app.delta_time * 0.6);
    }
    let status = app.hands_frame.status.lock().unwrap().clone();
    if status.open_id != app.hands_frame.open_id {
        app.hands_frame.open_id = status.open_id;
        app.tasks
            .enqueue(TaskType::Overlay(OverlayTask::Drop(OverlaySelector::Name(
                PANEL.into(),
            ))));
        app.tasks.enqueue(TaskType::Overlay(OverlayTask::Spawn(
            OverlaySelector::Name(PANEL.into()),
            SpawnPos::FixedNoRealign,
            Box::new(|app| create(app, false).ok()),
        )));
    }
    if let Some(id) = overlays.lookup(BOOKMARKS) {
        if let Some(overlay) = overlays.mut_by_id(id) {
            if let Some(state) = overlay.config.active_state.as_mut() {
                state.alpha = if status.bookmarks > 0 { 1. } else { 0. };
            }
        }
    }

    let hands = app.input_state.pointers.each_ref().map(|p| {
        if p.hand_tracking {
            p.frame_hand.unwrap_or_default()
        } else {
            Hand {
                position: p.pose.translation,
                hold: p.now.grab,
                trigger: p.now.click,
                valid: p.tracked && !p.cancel_interaction,
                armable: true,
            }
        }
    });
    let blocked = app.watch_menu.open().is_some()
        || app.input_state.pointers.iter().any(|p| p.frame_blocked());
    let owned = app.hands_frame.update(
        app.input_state.frame_view.unwrap_or(app.input_state.hmd),
        hands,
        blocked,
    );
    let hide = owned || app.hands_frame.capturing();
    for name in [PANEL, BOOKMARKS] {
        if let Some(id) = overlays.lookup(name) {
            if let Some(overlay) = overlays.mut_by_id(id) {
                if let Some(state) = overlay.config.active_state.as_mut() {
                    state.alpha = if hide || (name == BOOKMARKS && status.bookmarks == 0) {
                        0.
                    } else {
                        1.
                    };
                }
            }
        }
    }
    if let Some(id) = overlays.lookup(GUIDE) {
        if let Some(overlay) = overlays.mut_by_id(id) {
            if let Some(state) = overlay.config.active_state.as_mut() {
                if app.hands_frame.enabled
                    && app.hands_frame.model.active
                    && !app.hands_frame.model.hide_guide
                {
                    if let Some(Rect { bounds, depth }) = app.hands_frame.model.rect {
                        let center = Vec3A::new(
                            (bounds[0] + bounds[2]) * 0.5 * depth,
                            (bounds[1] + bounds[3]) * 0.5 * depth,
                            -depth,
                        );
                        let extent =
                            ((bounds[2] - bounds[0]).max(bounds[3] - bounds[1]) * depth * 1.0204)
                                .max(0.01);
                        state.transform = app.input_state.frame_view.unwrap_or(app.input_state.hmd)
                            * Affine3A::from_scale_rotation_translation(
                                Vec3::splat(extent),
                                Quat::IDENTITY,
                                center.into(),
                            );
                        state.alpha = 1.;
                    }
                } else {
                    state.alpha = 0.;
                }
            }
        }
    }
    owned
}
#[cfg(feature = "openxr")]
pub fn tracked_hand(joints: Option<&openxr::HandJointLocations>, previous: bool) -> Option<Hand> {
    let j = joints?;
    let flags = openxr::SpaceLocationFlags::POSITION_VALID;
    if j.iter().any(|p| !p.location_flags.contains(flags))
        || !j[1]
            .location_flags
            .contains(openxr::SpaceLocationFlags::POSITION_TRACKED)
    {
        return None;
    }
    let p = |i: usize| {
        Vec3A::new(
            j[i].pose.position.x,
            j[i].pose.position.y,
            j[i].pose.position.z,
        )
    };
    if (0..26).any(|i| !p(i).is_finite()) {
        return None;
    }
    let span = p(7).distance(p(22));
    if !(0.03..=0.13).contains(&span) {
        return None;
    }
    let curled = [12usize, 17, 22].iter().all(|&base| {
        let length = (base..base + 3)
            .map(|i| p(i).distance(p(i + 1)))
            .sum::<f32>();
        length > 0.025 && p(base).distance(p(base + 3)) < length * 0.75
    });
    let trigger = p(5).distance(p(10)) < span * if previous { 0.38 } else { 0.22 };
    let index = p(10) - p(7);
    let thumb = p(5) - p(7);
    let length = (7..10).map(|i| p(i).distance(p(i + 1))).sum::<f32>();
    let armable = curled
        && index.length() > length * 0.85
        && thumb.length() > span * 0.3
        && index
            .normalize_or_zero()
            .dot(thumb.normalize_or_zero())
            .abs()
            < 0.8;
    Some(Hand {
        armable,
        position: p(7),
        hold: curled,
        trigger,
        valid: true,
    })
}

fn create_bookmarks(app: &mut AppState) -> anyhow::Result<OverlayWindowConfig> {
    let mut panel = GuiPanel::new_from_template(
        app,
        "gui/hands-frame-bookmarks.xml",
        (),
        NewGuiPanelParams::default(),
    )?;
    let button = panel
        .parser_state
        .fetch_component_as::<ComponentButton>("restore")?;
    let sender = app.hands_frame.sender();
    button.on_click(Rc::new(move |_, _| {
        if let Some(tx) = &sender {
            let _ = tx.try_send(serde_json::json!({"op":"bookmarks"}));
        }
        Ok(())
    }));
    let id = panel.parser_state.get_widget_id("thumb")?;
    let previous = std::cell::Cell::new(u64::MAX);
    panel.add_event_listener(
        id,
        EventListenerKind::InternalStateChange,
        Box::new(move |common, data, app, _| {
            let state = app.hands_frame.status.lock().unwrap().clone();
            if previous.get() != state.revision {
                previous.set(state.revision);
                data.obj.get_as_mut::<WidgetImage>().unwrap().set_content(
                    common.alterables,
                    state.bookmark_png.as_ref().and_then(|b| {
                        CustomGlyphData::from_bytes_raster(
                            &app.wgui_globals,
                            &state.bookmark_preview,
                            b,
                        )
                        .ok()
                    }),
                );
            }
            Ok(EventResult::Pass)
        }),
    );
    panel
        .timers
        .push(GuiTimer::new(Duration::from_millis(250), 0));
    panel.update_layout(app)?;
    let rotation = crate::overlays::watch::WATCH_ROT;
    Ok(OverlayWindowConfig {
        name: BOOKMARKS.into(),
        global: true,
        z_order: Z_ORDER_HELP,
        show_on_spawn: true,
        default_state: OverlayWindowState {
            interactable: true,
            grabbable: true,
            alpha: 0.,
            positioning: Positioning::FollowHand {
                hand: if app.input_state.space_drag_hand == 1 {
                    wlx_common::common::LeftRight::Right
                } else {
                    wlx_common::common::LeftRight::Left
                },
                lerp: 1.,
            },
            transform: Affine3A::from_scale_rotation_translation(
                Vec3::splat(0.16),
                rotation,
                crate::overlays::watch::WATCH_POS + rotation * Vec3::new(-0.20, 0., 0.),
            ),
            ..Default::default()
        },
        ..OverlayWindowConfig::from_backend(Box::new(panel))
    })
}

#[cfg(all(test, feature = "openxr"))]
mod tests {
    use super::*;
    #[test]
    fn framing_hand_leaves_index_free_and_pinch_has_hysteresis() {
        use openxr::{HandJointLocation, Posef, SpaceLocationFlags as Flags, Vector3f};
        let joint = HandJointLocation {
            location_flags: Flags::POSITION_VALID
                | Flags::POSITION_TRACKED
                | Flags::ORIENTATION_VALID,
            pose: Posef::IDENTITY,
            radius: 0.01,
        };
        let mut j = [joint; 26];
        for (base, x) in [(7, 0.04), (12, 0.01), (17, -0.015), (22, -0.04)] {
            for (i, (y, z)) in [(0., 0.), (0.025, 0.), (0.025, 0.025), (0., 0.01)]
                .iter()
                .copied()
                .enumerate()
            {
                j[base + i].pose.position = Vector3f { x, y, z };
            }
        }
        j[8].pose.position = Vector3f {
            x: 0.04,
            y: 0.025,
            z: 0.,
        };
        j[9].pose.position = Vector3f {
            x: 0.04,
            y: 0.05,
            z: 0.,
        };
        j[10].pose.position = Vector3f {
            x: 0.04,
            y: 0.08,
            z: 0.,
        };
        j[5].pose.position = Vector3f {
            x: 0.1,
            y: 0.,
            z: 0.,
        };
        let hand = tracked_hand(Some(&j), false).unwrap();
        assert!(hand.hold && hand.armable && !hand.trigger);
        j[5].pose.position = j[10].pose.position;
        assert!(tracked_hand(Some(&j), false).unwrap().trigger);
        j[5].pose.position.x += 0.025;
        assert!(!tracked_hand(Some(&j), false).unwrap().trigger);
        assert!(tracked_hand(Some(&j), true).unwrap().trigger);
        j[15].location_flags = Flags::from_raw(0);
        assert!(tracked_hand(Some(&j), false).is_none());
        assert!(tracked_hand(None, false).is_none());
    }
}
