// SPDX-License-Identifier: GPL-3.0-only
// No OpenXR instance, Vulkan device, desktop portal or compositor is created.
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
