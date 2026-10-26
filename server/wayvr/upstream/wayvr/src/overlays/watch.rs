use crate::watch_menu::{self, Action, ButtonId};
use crate::{
    gui::{
        panel::{
            GuiPanel, NewGuiPanelParams, apply_custom_command, device_list::DeviceList,
            overlay_list::OverlayList, set_list::SetList,
        },
        timer::GuiTimer,
    },
    state::AppState,
    windowing::{Z_ORDER_WATCH, backend::OverlayEventData, window::OverlayWindowConfig},
};
use glam::{Affine3A, Quat, Vec3, vec3};
use slotmap::Key;
use std::time::Duration;
use wgui::{
    assets::AssetPathRef,
    components::button::ComponentButton,
    parser::{Fetchable, ParseDocumentParams},
};
use wgui::{
    event::{Event, MouseButtonIndex},
    layout::{Layout, WidgetID},
    parser::TemplateParams,
    widget::label::WidgetLabel,
};
use wlx_common::{
    common::LeftRight,
    windowing::{OverlayWindowState, Positioning},
};

pub const WATCH_NAME: &str = "watch";
pub const WATCH_POS: Vec3 = vec3(-0.03, -0.01, 0.125);
pub const WATCH_ROT: Quat = Quat::from_xyzw(-0.707_106_6, 0.000_796_361_8, 0.707_106_6, 0.0);

#[derive(Default)]
struct WatchState {
    device_list: DeviceList,
    overlay_list: OverlayList,
    set_list: SetList,
    clock_12h: bool,
    motoc: bool,
}

pub fn create_watch(app: &mut AppState) -> anyhow::Result<OverlayWindowConfig> {
    let state = WatchState {
        clock_12h: app.session.config.clock_12h,
        motoc: app.shora_battery.motoc_mode(),
        ..Default::default()
    };
    let watch_xml = if app.shora_battery.motoc_mode() {
        "gui/watch-motoc.xml"
    } else {
        "gui/watch.xml"
    };

    let mut panel =
        GuiPanel::new_from_template(app, watch_xml, state, NewGuiPanelParams::default())?;

    sets_or_overlays(&mut panel, app);

    let doc_params = ParseDocumentParams {
        globals: panel.layout.state.globals.clone(),
        path: AssetPathRef::FileOrBuiltIn(watch_xml),
        extra: panel.doc_extra.take().unwrap_or_default(),
    };

    setup_fan(&mut panel, app, &doc_params)?;

    panel.on_notify = Some(Box::new({
        let name = WATCH_NAME;
        move |panel, app, event_data| {
            let mut elems_changed = panel.state.overlay_list.on_notify(
                &mut panel.layout,
                &mut panel.parser_state,
                &event_data,
                &doc_params,
            )?;

            elems_changed |= panel.state.set_list.on_notify(
                &mut panel.layout,
                &mut panel.parser_state,
                &event_data,
                &doc_params,
            )?;

            elems_changed |= panel.state.device_list.on_notify(
                app,
                &mut panel.layout,
                &mut panel.parser_state,
                &event_data,
                &doc_params,
            )?;

            if app.shora_battery.motoc_mode() != panel.state.motoc {
                panel.state.motoc = app.shora_battery.motoc_mode();
                for button in watch_menu::buttons(panel.state.motoc) {
                    if let Ok(component) = panel
                        .parser_state
                        .fetch_component_as::<ComponentButton>(button.widget)
                    {
                        component.set_text(
                            &mut panel.layout.common(),
                            wgui::i18n::Translation::from_raw_text(button.title),
                        );
                    }
                }
            }
            match event_data {
                OverlayEventData::EditModeChanged(edit_mode) => {
                    if let Ok(btn_edit_mode) = panel
                        .parser_state
                        .fetch_component_as::<ComponentButton>("btn_edit_mode")
                    {
                        btn_edit_mode.set_sticky_state(&mut panel.layout.common(), edit_mode);
                    }
                }
                OverlayEventData::SettingsChanged => {
                    panel.layout.mark_redraw();
                    sets_or_overlays(panel, app);

                    if app.session.config.clock_12h != panel.state.clock_12h {
                        panel.state.clock_12h = app.session.config.clock_12h;

                        let clock_root = panel.parser_state.get_widget_id("clock_root")?;
                        panel.layout.remove_children(clock_root);

                        panel.parser_state.instantiate_template(
                            &doc_params,
                            "Clock",
                            &mut panel.layout,
                            clock_root,
                            Default::default(),
                        )?;

                        elems_changed = true;
                    }
                }
                OverlayEventData::CustomCommand { element, command } => {
                    if let Err(e) = apply_custom_command(panel, app, &element, &command) {
                        log::warn!("Could not apply {command:?} on {name}/{element}: {e:?}");
                    } else {
                        elems_changed = true;
                    }
                }
                _ => {}
            }

            if elems_changed {
                panel.process_custom_elems(app);
            }

            Ok(())
        }
    }));

    panel
        .timers
        .push(GuiTimer::new(Duration::from_millis(100), 0));

    let positioning = Positioning::FollowHand {
        hand: LeftRight::Left,
        lerp: 1.0,
    };

    panel.max_size = glam::Vec2::new(watch_menu::WIDTH, watch_menu::HEIGHT);
    panel.update_layout(app)?;

    Ok(OverlayWindowConfig {
        name: WATCH_NAME.into(),
        z_order: Z_ORDER_WATCH,
        default_state: OverlayWindowState {
            grabbable: false,
            interactable: true,
            positioning,
            transform: Affine3A::from_scale_rotation_translation(
                Vec3::ONE * 0.2,
                WATCH_ROT,
                WATCH_POS,
            ),
            angle_fade: true,
            ..OverlayWindowState::default()
        },
        show_on_spawn: app.session.config.enable_watch,
        global: true,
        ..OverlayWindowConfig::from_backend(Box::new(panel))
    })
}

fn sets_or_overlays(panel: &mut GuiPanel<WatchState>, app: &mut AppState) {
    let visible = if app.domed.active() { [false,false] } else if app.session.config.sets_on_watch {
        [false, true]
    } else {
        [true, false]
    };

    let widget = [
        panel
            .parser_state
            .get_widget_id("panels_root")
            .unwrap_or_default(),
        panel
            .parser_state
            .get_widget_id("sets_root")
            .unwrap_or_default(),
    ];

    for i in 0..2 {
        panel
            .layout
            .alterables
            .set_widget_visible(widget[i], visible[i]);
    }
}

fn setup_fan(
    panel: &mut GuiPanel<WatchState>,
    app: &mut AppState,
    doc: &ParseDocumentParams,
) -> anyhow::Result<()> {
    app.watch_menu.close();
    let base = panel.parser_state.get_widget_id("watch_base")?;
    let heading = panel.parser_state.get_widget_id("fan_heading")?;
    let title = panel
        .parser_state
        .fetch_widget(&panel.layout.state, "fan_title")?
        .widget;
    let controls = build_fan_controls(
        &mut panel.layout,
        &mut panel.parser_state,
        doc,
        panel.state.motoc,
    )?;
    panel.process_custom_elems(app);
    let parents = controls
        .iter()
        .filter(|(spec, _)| matches!(spec.id, ButtonId::Parent(_)))
        .map(|(spec, _)| {
            Ok((
                spec.group,
                panel
                    .parser_state
                    .fetch_component_as::<ComponentButton>(spec.widget)?,
            ))
        })
        .collect::<anyhow::Result<Vec<_>>>()?;
    let toggles = [
        (Action::HandTracking,panel.parser_state.fetch_component_as::<ComponentButton>("btn_hand_tracking")?),
        (
            Action::HandsFrame,
            panel
                .parser_state
                .fetch_component_as::<ComponentButton>("btn_hands_frame")?,
        ),
        (
            Action::Walking,
            panel
                .parser_state
                .fetch_component_as::<ComponentButton>("btn_walking")?,
        ),
        (
            Action::BodyTurning,
            panel
                .parser_state
                .fetch_component_as::<ComponentButton>("btn_body_turning")?,
        ),
        (
            Action::Dpad,
            panel
                .parser_state
                .fetch_component_as::<ComponentButton>("btn_hand_joysticks")?,
        ),
        (
            Action::HandControllers,
            panel
                .parser_state
                .fetch_component_as::<ComponentButton>("btn_hand_controllers")?,
        ),
    ];
    let domed_widgets=["panels_root","sets_root","domed_controls"].map(|id|panel.parser_state.get_widget_id(id).unwrap_or_default());
    let mut displayed = None;
    panel.on_event_filter = Some(Box::new(move |layout, state, app, event| {
        // Use actual WGUI boundaries, not idealized fan geometry: theme padding,
        // font metrics and UI scaling must not change which button receives a tap.
        let target = |layout: &Layout, pos: glam::Vec2, menu: &watch_menu::Menu| {
            controls.iter().find_map(|(spec, id)| {
                if !menu.visible(spec.id, spec.group) {
                    return None;
                }
                let node = *layout.state.nodes.get(*id)?;
                let mut boundary = layout.state.get_node_boundary(node);
                let mut parent = layout.state.tree.parent(node);
                while let Some(node) = parent {
                    boundary.pos += layout.state.get_node_boundary(node).pos;
                    parent = layout.state.tree.parent(node);
                }
                boundary.contains_point(pos).then_some(spec.id)
            })
        };
        let domed=app.domed.active();
        for (i,id) in domed_widgets.iter().enumerate(){if !id.is_null(){layout.alterables.set_widget_visible(*id,if i==2{domed}else{!domed&&if i==0{!app.session.config.sets_on_watch}else{app.session.config.sets_on_watch}});}}
        let mut forward = true;
        match event {
            Event::MouseDown(e) => {
                let hit = target(layout, e.pos, &app.watch_menu);
                if let Some(pointer) = e.device.to_index() {
                    if matches!(e.index, MouseButtonIndex::Left) {
                        app.watch_menu.press(pointer, hit);
                    }
                }
                if hit.is_some() {
                    forward = matches!(e.index, MouseButtonIndex::Left);
                } else if app.watch_menu.open().is_some() {
                    forward = false;
                }
            }
            Event::MouseUp(e) => {
                let hit = target(layout, e.pos, &app.watch_menu);
                let modal = app.watch_menu.open().is_some();
                if matches!(e.index, MouseButtonIndex::Left) {
                    if let Some(pointer) = e.device.to_index() {
                        if let Some(action) = app.watch_menu.release(pointer, hit) {
                            trigger_action(app, action);
                        }
                    }
                }
                if hit.is_some() {
                    forward = matches!(e.index, MouseButtonIndex::Left);
                } else if modal {
                    forward = false;
                }
            }
            Event::MouseLeave(e) => {
                if let Some(pointer) = e.device.to_index() {
                    app.watch_menu.cancel_pointer(pointer);
                }
            }
            Event::MouseCancel => app.watch_menu.close(),
            _ => {}
        }
        for (action, button) in &toggles {
            let enabled = match action {
                Action::Walking => app.input_state.walking_enabled,
                Action::BodyTurning => app.input_state.body_turning_enabled,
                Action::HandsFrame => app.hands_frame.enabled,
                Action::Dpad => app.input_state.joysticks_enabled,
                Action::HandControllers => app.input_state.hand_controllers_enabled,
                Action::HandTracking => app.input_state.hand_tracking_enabled,
                _ => false,
            };
            button.set_sticky_state(&mut layout.common(), enabled);
        }
        if displayed != app.watch_menu.open() {
            // Cancel native pressed visuals as well as the model's armed taps.
            // Dispatch directly so this cancellation does not close the new group.
            let _ = layout.push_event(&Event::MouseCancel, app, state);
            displayed = app.watch_menu.open();
            layout
                .alterables
                .set_widget_visible(base, displayed.is_none());
            layout
                .alterables
                .set_widget_visible(heading, displayed.is_some());
            for (spec, id) in &controls {
                layout
                    .alterables
                    .set_widget_visible(*id, app.watch_menu.visible(spec.id, spec.group));
            }
            for (group, button) in &parents {
                button.set_sticky_state(&mut layout.common(), displayed == Some(*group));
            }
            if let Some(group) = displayed {
                if let Ok(mut label) = title.cast::<WidgetLabel>() {
                    label.set_text(
                        &mut layout.common(),
                        wgui::i18n::Translation::from_raw_text(group.name()),
                    );
                }
            }
            layout.mark_redraw();
        }
        forward
    }));
    Ok(())
}

fn trigger_action(app: &mut AppState, action: Action) {
    use crate::backend::task::{OverlayTask, PlayspaceTask, TaskType, ToggleMode};
    use crate::windowing::OverlaySelector;
    let playspace = match action {
        Action::Recenter => Some(PlayspaceTask::Recenter),
        Action::RememberFloor => Some(PlayspaceTask::RememberFloor),
        Action::ForgetFloor => Some(PlayspaceTask::ForgetFloor),
        _ => None,
    };
    if let Some(task) = playspace {
        app.tasks.enqueue(TaskType::Playspace(task));
        return;
    }
    match action {
        Action::FixFloor => crate::gui::panel::button::queue_fix_floor(app),
        Action::FullReset => app
            .shora_battery
            .request_reset(crate::shora_battery::ResetKind::Full),
        Action::MountingReset => app
            .shora_battery
            .request_reset(crate::shora_battery::ResetKind::Mounting),
        Action::Keyboard => app
            .tasks
            .enqueue(TaskType::Overlay(OverlayTask::ToggleOverlay(
                OverlaySelector::Name("kbd".into()),
                ToggleMode::Toggle,
            ))),
        Action::Walking => app.input_state.walking_enabled = !app.input_state.walking_enabled,
        Action::BodyTurning => {
            app.input_state.body_turning_enabled = !app.input_state.body_turning_enabled
        }
        Action::WalkingSetup => app
            .shora_battery
            .request_reset(crate::shora_battery::ResetKind::Walking),
        Action::Dpad => app.input_state.joysticks_enabled = !app.input_state.joysticks_enabled,
        Action::HandTracking => app.input_state.hand_tracking_enabled = !app.input_state.hand_tracking_enabled,
        Action::HandControllers => {
            app.input_state.hand_controllers_enabled = !app.input_state.hand_controllers_enabled
        }
        Action::HandsFrame => crate::hands_frame_panel::toggle(app),
        Action::Performance => crate::performance_panel::toggle(app),
        Action::Configuration => app
            .tasks
            .enqueue(TaskType::Overlay(OverlayTask::ToggleDashboard)),
        Action::Edit => app
            .tasks
            .enqueue(TaskType::Overlay(OverlayTask::ToggleEditMode)),
        _ => {}
    }
}

/// Native layout construction shared by the watch and its offline layout/preview test.
pub(crate) fn build_fan_controls(
    layout: &mut Layout,
    parser: &mut wgui::parser::ParserState,
    doc: &ParseDocumentParams,
    motoc: bool,
) -> anyhow::Result<Vec<(watch_menu::Button, WidgetID)>> {
    let root = parser.get_widget_id("fan_controls")?;
    let mut controls = Vec::new();
    for spec in watch_menu::buttons(motoc) {
        let mut params = TemplateParams::new();
        for (key, value) in [
            ("id", spec.widget.to_owned()),
            ("x", spec.x.to_string()),
            ("y", spec.y.to_string()),
            ("title", spec.title.to_owned()),
            ("icon", spec.icon.to_owned()),
        ] {
            params.insert_rc(key, value.into());
        }
        parser.instantiate_template(doc, "FanButton", layout, root, params)?;
        let widget = parser
            .fetch_component_as::<ComponentButton>(spec.widget)?
            .get_rect();
        layout
            .alterables
            .set_widget_visible(widget, matches!(spec.id, ButtonId::Parent(_)));
        controls.push((spec, widget));
    }
    Ok(controls)
}
