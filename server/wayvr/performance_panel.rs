// SPDX-License-Identifier: GPL-3.0-or-later
use crate::performance::{monotonic_ns, summary};
use crate::{
    backend::task::{OverlayTask, SpawnPos, TaskType},
    gui::{
        panel::{GuiPanel, NewGuiPanelParams},
        timer::GuiTimer,
    },
    state::AppState,
    windowing::{OverlaySelector, Z_ORDER_WATCH, window::OverlayWindowConfig},
};
use glam::{Affine3A, Quat, Vec3, vec3};
use std::time::Duration;
use wgui::{
    components::bar_graph::{ComponentBarGraph, ValueCell},
    drawing::Color,
    event::EventListenerKind,
    i18n::Translation,
    parser::Fetchable,
    widget::{EventResult, label::WidgetLabel},
};
use wlx_common::{
    common::LeftRight,
    windowing::{OverlayWindowState, Positioning},
};
pub const NAME: &str = "wivrn-performance";

pub fn toggle(app: &mut AppState) {
    app.performance.mode = (app.performance.mode + 1) % 3;
    let mode = app.performance.mode;
    let selector = OverlaySelector::Name(NAME.into());
    app.tasks
        .enqueue(TaskType::Overlay(OverlayTask::Drop(selector.clone())));
    if mode != 0 {
        app.tasks.enqueue(TaskType::Overlay(OverlayTask::Spawn(
            selector,
            SpawnPos::FixedNoRealign,
            Box::new(move |app| {
                create(app, mode)
                    .inspect_err(|e| log::warn!("Performance panel: {e:#}"))
                    .ok()
            }),
        )));
    }
    log::info!(
        "Performance overlay: {}",
        ["off", "wrist", "floating"][mode as usize]
    );
}
fn create(app: &mut AppState, mode: u8) -> anyhow::Result<OverlayWindowConfig> {
    let mut panel =
        GuiPanel::new_from_template(app, "gui/performance.xml", (), NewGuiPanelParams::default())?;
    let graph = panel
        .parser_state
        .fetch_component_as::<ComponentBarGraph>("frames")?;
    let id = panel.parser_state.get_widget_id("metrics")?;
    let previous = std::cell::Cell::new(None);
    let source = std::cell::Cell::new(None);
    panel.add_event_listener(
        id,
        EventListenerKind::InternalStateChange,
        Box::new(move |common, data, app, _| {
            let snapshot = app.performance.snapshot();
            let now = monotonic_ns();
            let label = data.obj.get_as_mut::<WidgetLabel>().unwrap();
            label.set_text(
                common,
                Translation::from_raw_text(&summary(snapshot.as_ref(), now)),
            );
            let sample = snapshot
                .as_ref()
                .and_then(|s| s.busiest(now).map(|a| (s, a)));
            let identity = sample.map(|(s, a)| (s.pid, s.session, a.id));
            if identity != source.get() {
                // ComponentBarGraph has no clear operation. Fill its bounded history
                // with empty cells so a restarted/changed XR source is not mixed in.
                for _ in 0..150 {
                    graph.push_value(ValueCell {
                        value: 0.0,
                        color: Color::new(0.0, 0.0, 0.0, 0.0),
                    });
                }
                source.set(identity);
                previous.set(None);
            }
            if let Some((s, a)) = sample {
                let stamp = (s.updated_ns, a.updated_ns);
                if previous.get() != Some(stamp) {
                    let over = a.interval_ms > a.period_ms * 1.1;
                    graph.set_limits(common, (0.0, (a.period_ms * 3.0).ceil().max(20.0)));
                    graph.push_value(ValueCell {
                        value: a.interval_ms,
                        color: if over {
                            Color::new(0.95, 0.3, 0.3, 1.0)
                        } else {
                            Color::new(0.3, 0.85, 0.7, 1.0)
                        },
                    });
                    previous.set(Some(stamp));
                }
            }
            common.alterables.mark_redraw();
            Ok(EventResult::Pass)
        }),
    );
    let id = panel.parser_state.get_widget_id("host")?;
    panel.add_event_listener(
        id,
        EventListenerKind::InternalStateChange,
        Box::new(|common, data, app, _| {
            data.obj.get_as_mut::<WidgetLabel>().unwrap().set_text(
                common,
                Translation::from_raw_text(&app.performance.host_text()),
            );
            Ok(EventResult::Pass)
        }),
    );
    panel
        .timers
        .push(GuiTimer::new(Duration::from_millis(200), 0));
    panel.update_layout(app)?;
    let (positioning, rotation, position, width) = if mode == 1 {
        let hand = if app.input_state.space_drag_hand == 1 {
            LeftRight::Right
        } else {
            LeftRight::Left
        };
        let rotation = crate::overlays::watch::WATCH_ROT;
        (
            Positioning::FollowHand { hand, lerp: 1.0 },
            rotation,
            crate::overlays::watch::WATCH_POS + rotation * vec3(0.0, 0.18, 0.0),
            0.30,
        )
    } else {
        (
            Positioning::Floating,
            Quat::IDENTITY,
            vec3(0.30, -0.15, -0.95),
            0.50,
        )
    };
    Ok(OverlayWindowConfig {
        name: NAME.into(),
        z_order: Z_ORDER_WATCH,
        global: true,
        show_on_spawn: true,
        default_state: OverlayWindowState {
            interactable: false,
            grabbable: false,
            block_input: false,
            positioning,
            angle_fade: mode == 1,
            transform: Affine3A::from_scale_rotation_translation(
                Vec3::ONE * width,
                rotation,
                position,
            ),
            ..Default::default()
        },
        ..OverlayWindowConfig::from_backend(Box::new(panel))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn performance_layout_and_watch_toggle_parse_without_xr() {
        let globals = wgui::globals::WguiGlobals::new(
            Box::new(crate::gui::asset::GuiAsset {}),
            &wlx_common::locale::WayVRLangProvider::default(),
            &wgui::font_config::WguiFontConfig::default(),
            std::path::PathBuf::from("/nonexistent"),
            wgui::palette::PALETTES[0].1.clone(),
        )
        .unwrap();
        for path in ["gui/performance.xml", "gui/watch.xml", "gui/watch-motoc.xml"] {
            let (mut layout, mut parser) = wgui::parser::new_layout_from_assets(
                &wgui::parser::ParseDocumentParams {
                    globals: globals.clone(),
                    path: wgui::assets::AssetPathRef::BuiltIn(path),
                    extra: Default::default(),
                },
                wgui::layout::LayoutParams {
                    resize_to_parent: false,
                    theme: Default::default(),
                },
            )
            .unwrap();
            if path.ends_with("watch.xml") {
                let root = parser.get_widget_id("devices_root").unwrap();
                for (template, key, value) in [
                    ("Hmd", "idx", "0"),
                    ("LeftHand", "idx", "1"),
                    ("RightHand", "idx", "2"),
                    ("ShoraTracker", "key", "chest"),
                    ("ShoraTracker", "key", "hip"),
                    ("ShoraTracker", "key", "left"),
                    ("ShoraTracker", "key", "right"),
                ] {
                    let mut params = wgui::parser::TemplateParams::new();
                    params.insert_rc(key, value.into());
                    parser
                        .instantiate_template(
                            &wgui::parser::ParseDocumentParams {
                                globals: globals.clone(),
                                path: wgui::assets::AssetPathRef::BuiltIn(path),
                                extra: Default::default(),
                            },
                            template,
                            &mut layout,
                            root,
                            params,
                        )
                        .unwrap();
                }
            }
            if path.contains("watch") {
                crate::overlays::watch::build_fan_controls(
                    &mut layout, &mut parser,
                    &wgui::parser::ParseDocumentParams {
                        globals: globals.clone(), path: wgui::assets::AssetPathRef::BuiltIn(path), extra: Default::default(),
                    }, path.contains("motoc"),
                ).unwrap();
            }
            layout
                .update(&mut wgui::layout::LayoutUpdateParams {
                    size: glam::vec2(2048.0, 2048.0),
                    timestep_alpha: 1.0,
                })
                .unwrap();
            assert!(layout.content_size.x > 100.0 && layout.content_size.y > 100.0);
            assert!(layout.content_size.x < 1200.0 && layout.content_size.y < 1600.0);
            if path.ends_with("performance.xml") {
                parser
                    .fetch_component_as::<ComponentBarGraph>("frames")
                    .unwrap();
                for id in ["metrics", "host"] {
                    parser.get_widget_id(id).unwrap();
                }
            } else {
                for id in ["btn_performance", "btn_full_reset", "btn_mounting_reset"] {
                    parser.fetch_component_as::<wgui::components::button::ComponentButton>(id).unwrap();
                }
                parser.get_widget_id("tracking_reset_status").unwrap();
            }
            eprintln!(
                "{path}: {} x {}",
                layout.content_size.x, layout.content_size.y
            );
        }
    }
}
