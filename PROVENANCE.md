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
