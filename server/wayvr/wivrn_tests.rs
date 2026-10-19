// SPDX-License-Identifier: GPL-3.0-only
// No OpenXR instance, Vulkan device, desktop portal or compositor is created.
#[test]
fn room_fields_preserve_existing_layout_and_playspace_settings() {
    use glam::{Affine3A, Vec3};
    use wlx_common::windowing::{OverlayWindowState, RoomBinding};
    let old_window: OverlayWindowState = serde_json::from_str("{}").unwrap();
    assert!(old_window.room_binding.is_none());
    let mut window = old_window;
    window.room_binding = Some(RoomBinding {
        uuid: "1234567890abcdef1234567890abcdef".into(),
        relative: Affine3A::from_translation(Vec3::new(1.0, 2.0, 3.0)),
    });
    let saved = serde_json::to_string(&window).unwrap();
    let restored: OverlayWindowState = serde_json::from_str(&saved).unwrap();
    assert_eq!(restored.room_binding.unwrap().relative.translation, Vec3::new(1.0,2.0,3.0).into());

    let state = crate::state::PlayspaceState::default();
    let mut old = serde_json::to_value(&state).unwrap();
    old.as_object_mut().unwrap().remove("remembered_floor");
    let restored: crate::state::PlayspaceState = serde_json::from_value(old).unwrap();
    assert!(restored.remembered_floor.is_none());
    assert_eq!(restored.openxr_space_center, state.openxr_space_center);
}

#[test]
fn existing_wayvr_profile_is_respected() {
    if std::env::var_os("WIVRN_TEST_WAYVR_PROFILE").is_none() {
        return;
    }
    let root = wlx_common::config_io::get_config_root();
    assert_eq!(root, std::path::PathBuf::from(std::env::var_os("XDG_CONFIG_HOME").unwrap()).join("wayvr"));
    let config = crate::config::load_general_config();
    let value = serde_json::to_value(config).unwrap();
    let saved = root.join("conf.d/zz-saved-config.json5");
    let expected: serde_json::Value = json5::from_str(&std::fs::read_to_string(saved).unwrap()).unwrap();
    for (key, expected) in expected.as_object().unwrap() {
        assert!(value.get(key).is_some_and(|actual| same(actual, expected)), "saved option {key} changed");
    }
}

fn same(actual: &serde_json::Value, expected: &serde_json::Value) -> bool {
    use serde_json::Value;
    match (actual, expected) {
        (Value::Number(a), Value::Number(b)) => {
            let (a, b) = (a.as_f64().unwrap(), b.as_f64().unwrap());
            (a-b).abs() <= 1e-6 * b.abs().max(1.0) // f32 configuration fields round on deserialize.
        }
        (Value::Array(a), Value::Array(b)) => a.len() == b.len() && a.iter().zip(b).all(|(a,b)| same(a,b)),
        (Value::Object(a), Value::Object(b)) => a.len() == b.len() && b.iter().all(|(k,b)| a.get(k).is_some_and(|a| same(a,b))),
        _ => actual == expected,
    }
}

#[test]
fn grouped_watch_native_layout_and_preview() {
    use std::rc::Rc;
    use glam::Vec2;
    use wgui::{
        assets::AssetPathRef, font_config::WguiFontConfig, globals::WguiGlobals,
        layout::{LayoutParams, LayoutUpdateParams}, palette::WguiColorPalette,
        parser::{Fetchable, ParseDocumentParams}, theme::WguiTheme,
    };
    use crate::watch_menu::{self, ButtonId};
    let globals = WguiGlobals::new(
        Box::new(crate::gui::asset::GuiAsset), &wlx_common::locale::WayVRLangProvider::default(),
        &WguiFontConfig::default(), std::path::PathBuf::new(), WguiColorPalette::get_builtin("Default"),
    ).unwrap();
    let doc = ParseDocumentParams { globals: globals.clone(), path: AssetPathRef::BuiltIn("gui/watch.xml"), extra: Default::default() };
    let (mut layout, mut parser) = wgui::parser::new_layout_from_assets(&doc,
        LayoutParams { resize_to_parent: false, theme: Rc::new(WguiTheme::default()) }).unwrap();
    let controls = crate::overlays::watch::build_fan_controls(&mut layout, &mut parser, &doc, false).unwrap();
    let base = parser.get_widget_id("watch_base").unwrap();
    let heading = parser.get_widget_id("fan_heading").unwrap();
    let mut states = Vec::new();
    for open in [None, Some(watch_menu::Group::Calibration), Some(watch_menu::Group::Input), Some(watch_menu::Group::Utils)] {
        layout.alterables.set_widget_visible(base, open.is_none());
        layout.alterables.set_widget_visible(heading, open.is_some());
        let mut visible = Vec::new();
        for (spec, id) in &controls {
            let show = open.map_or(matches!(spec.id, ButtonId::Parent(_)), |group| group == spec.group);
            layout.alterables.set_widget_visible(*id, show);
        }
        layout.update(&mut LayoutUpdateParams { size: Vec2::new(watch_menu::WIDTH, watch_menu::HEIGHT), timestep_alpha: 1.0 }).unwrap();
        assert_eq!(layout.content_size, Vec2::new(watch_menu::WIDTH, watch_menu::HEIGHT));
        for (spec, id) in &controls {
            let show = open.map_or(matches!(spec.id, ButtonId::Parent(_)), |group| group == spec.group);
            let node = *layout.state.nodes.get(*id).unwrap();
            assert_eq!(wgui::widget::is_node_visible(&layout.state.tree, node), show);
            if !show { continue; }
            let boundary = layout.state.get_widget_boundary(*id);
            assert!((boundary.size.x-watch_menu::BUTTON).abs()<0.1 && (boundary.size.y-watch_menu::BUTTON).abs()<0.1, "{} size {:?}",spec.widget,boundary.size);
            assert!((boundary.pos-Vec2::new(spec.x,spec.y)).length()<1.0, "{} {:?} != {},{}",spec.widget,boundary.pos,spec.x,spec.y);
            let mut measured=spec.clone();measured.x=boundary.pos.x;measured.y=boundary.pos.y;
            visible.push(measured);
        }
        states.push(serde_json::json!({"group":open,"buttons":visible}));
    }
    if let Some(path) = std::env::var_os("WIVRN_WATCH_LAYOUT_JSON") {
        let palette = WguiColorPalette::get_builtin("Default");
        use wgui::color::WguiColorName;
        let mut colors=serde_json::Map::new();
        for (name,key) in [("background",WguiColorName::Background),("button",WguiColorName::BackgroundVariant),("foreground",WguiColorName::OnBackground),("muted",WguiColorName::OnBackgroundVariant),("outline",WguiColorName::Outline),("primary",WguiColorName::Primary)] {
            let c=palette[key];colors.insert(name.into(),serde_json::json!([c.r,c.g,c.b,c.a]));
        }
        std::fs::write(path,serde_json::to_vec_pretty(&serde_json::json!({"width":watch_menu::WIDTH,"height":watch_menu::HEIGHT,"button_size":watch_menu::BUTTON,"states":states,"colors":colors})).unwrap()).unwrap();
    }
}

#[test]
fn hands_frame_native_panels_parse_and_have_all_controls() {
    use std::rc::Rc;
    use glam::Vec2;
    use wgui::{assets::AssetPathRef,font_config::WguiFontConfig,globals::WguiGlobals,
        layout::{LayoutParams,LayoutUpdateParams},palette::WguiColorPalette,parser::{Fetchable,ParseDocumentParams},theme::WguiTheme};
    let globals=WguiGlobals::new(Box::new(crate::gui::asset::GuiAsset),&wlx_common::locale::WayVRLangProvider::default(),&WguiFontConfig::default(),std::path::PathBuf::new(),WguiColorPalette::get_builtin("Default")).unwrap();
    for name in ["gui/hands-frame.xml","gui/hands-frame-guide.xml","gui/hands-frame-bookmarks.xml"] {
        let doc=ParseDocumentParams{globals:globals.clone(),path:AssetPathRef::BuiltIn(name),extra:Default::default()};
        let (mut layout,parser)=wgui::parser::new_layout_from_assets(&doc,LayoutParams{resize_to_parent:false,theme:Rc::new(WguiTheme::default())}).unwrap();
        layout.update(&mut LayoutUpdateParams{size:Vec2::splat(1200.),timestep_alpha:1.}).unwrap();
        assert!(layout.content_size.x>0. && layout.content_size.y>0.);
        if name=="gui/hands-frame.xml" {
            for action in ["save","copy","bookmark","bookmarks","previous","next","original","open","close","auto_scan","language","source","mirror_fov","translation_provider","ocr_settings"] {
                parser.fetch_component_as::<wgui::components::button::ComponentButton>(action).unwrap();
            }
        }
    }
}
