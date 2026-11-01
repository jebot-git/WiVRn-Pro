// SPDX-License-Identifier: GPL-3.0-or-later
/// WiVRn settings are ordinary desktop/DomeD windows, never WayVR overlays.
pub fn supported_application(executable: &str) -> bool {
    std::path::Path::new(executable)
        .file_name()
        .is_none_or(|name| name != "wivrn-pro-dashboard" && name != "wivrn_dashboard.py")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_dashboard_launches_and_preserves_other_apps() {
        for executable in [
            "wivrn-pro-dashboard",
            "/usr/local/bin/wivrn-pro-dashboard",
            "/home/user/.local/opt/wivrn-pro/bin/wivrn-pro-dashboard",
            "/usr/share/wivrn-pro/dashboard/wivrn_dashboard.py",
        ] {
            assert!(!supported_application(executable), "{executable}");
        }
        assert!(supported_application("/usr/bin/terminal"));
        assert!(supported_application("/usr/bin/my-dashboard"));
    }
}
