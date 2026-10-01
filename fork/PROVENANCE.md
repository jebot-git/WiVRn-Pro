# Source and licenses

PC server, Python clients and headless provider: MIT (LICENSE-MIT).
Quest source and quest/native anchor manager: GPL-3.0-or-later (quest/COPYING).
The anchor manager was extracted from the earlier VRM companion experiment; it
uses OpenXR directly. Its historical wivrn::anchor namespace is an internal name,
not a link-time or source-tree dependency on WiVRn.
OpenXR loader/headers: Apache-2.0; nlohmann-json headers: MIT. Supply SDK
artifacts when building the APK and retain their applicable distribution notices.
No avatar assets, room geometry, headset credentials, private signing keys or
runtime discovery files are tracked. Local .deps, .state and build folders are
ignored; they are not part of a clean clone.

## WiVRn native branch

`wivrn/` vendors the WiVRn v26.9 release source:
https://github.com/WiVRn/WiVRn/releases/tag/v26.9 (upstream release commit prefix
`bbc6e4c`). The imported tar archive SHA-256 is
`89f63bc6c89943f3571055381ee2f1aa744d3d11372c56188b21e39c8d8ef733`.
The pristine import is commit `6b3c128`; subsequent changes integrate this bridge.
WiVRn and the native bridge code are GPL-3.0-or-later; retain `wivrn/COPYING`
and upstream per-file notices. Bundled upstream assets/dependencies retain their
respective licenses and WiVRn's generated license inventory.

The native anchor manager and guided coordinator were copied from this project's
`quest/native/` implementation (standalone baseline `2de2823`); the native guide
uses UI-button instructions instead of the standalone X/A shortcuts. The native
bridge adds no third-party JSON dependency to the Quest client: its wire format
is a fixed-capacity WiVRn packet. The server uses WiVRn's existing nlohmann-json
dependency. Native sources, tests and scripts carry GPL-3.0-or-later notices;
legacy standalone files keep their existing license.
