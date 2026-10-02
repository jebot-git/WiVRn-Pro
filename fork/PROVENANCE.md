# Source and licenses

This fork is based on official WiVRn v26.9:
https://github.com/WiVRn/WiVRn/releases/tag/v26.9
(commit `bbc6e4cc36c355fa6180980abd231673dc15115d`). The original source archive
SHA-256 was `89f63bc6c89943f3571055381ee2f1aa744d3d11372c56188b21e39c8d8ef733`.
The pristine nested import is `6b3c128`; `e9b43ba2` moved WiVRn to the root and
`4e7a0bb3` joined official ancestry for future ordinary merges. WiVRn and the
native integration are GPL-3.0-or-later; retain root `COPYING` and per-file notices.
Bundled upstream assets/dependencies retain their respective licenses.

The anchor manager and guided capture coordinator originate from this project's
standalone baseline `2de2823`. Its native UI uses WiVRn's own Vulkan renderer and
ImGui. No Godot dependency or examples are part of this fork. No private room
geometry, keys, runtime discovery files or avatar assets were added.

The Shora reconnect bridge was imported from the local development checkout of
https://github.com/jebot-git/slimevr-server-rs at HEAD
`0112633fec00a9707132d399ce9aec168f1fd1d0`. That checkout contained local edits;
HEAD alone is not an exact source pin. `shora-import.json` records SHA-256 hashes
of the imported source inputs. Fork changes replace its CLI/signal lifecycle with
a static-library FFI and add private-directory/peer checks and lifecycle tests.
The original proxy/tests/protobuf build and vendored SolarXR protocol retain
MIT OR Apache-2.0 licensing. Copies of those licenses are in `server/shora/`,
and SolarXR retains its own license in `server/shora/vendor/solarxr-protocol/`.
Cargo.lock pins transitive Rust crates; those retain their individual licenses.

The full current native backend and Qt6 frontend are also imported from that
checkout, including its local edits. `shora-backend-import.json` records source
input hashes; HEAD alone does not reproduce this snapshot. Backend source and
skeletal-model vendor code live under `server/shora/backend`, Qt modules under
`dashboard/shora`. Fork adaptations add the library entry point, SIGTERM handling,
HMD expiry, profile-preservation tests and the unified dashboard. Future Shora
updates should diff against these hashes and retain the fork adapters/tests.
The existing source licenses and third-party attribution are preserved in
`server/shora/backend/{LICENSE-*,THIRD_PARTY.md,licenses/}`. Only the public example
avatar mapping fixture is included, never a user avatar or profile. OpenXR,
Monado, FFmpeg, Vulkan and other dependency notices remain applicable.

Upstream WayVR is imported from https://github.com/wayvr-org/wayvr at
`4590371a4c8f6a5b30e2e7a3fdb3b8af15b6d65e`. `wayvr-import.json` records the 723
source inputs and the hashes of the adapted files; the source patch is
kept under `patches/wayvr`. Git metadata and upstream CI/editor configuration are
excluded. This is a source build, with the original Cargo.lock and GPL-3.0-only
notices retained, not an AppImage extraction. Source is under `server/wayvr/upstream`;
component licenses are installed with the worker. Crates and bundled assets keep
their respective notices. The profile tests only read temporary local copies;
no user WayVR configuration or portal tokens are committed.

WayVR's hand gesture and pointing algorithm in `server/wayvr/hand_input.rs` is
adapted from the local KWin hand-tracking prototype's `vrhandpointer.cpp` and
`vrhandpointer.h`, with the interaction design from `qml/HandInput.qml`.
`kwin-hand-input-source.json` records the exact local source hashes, including
uncommitted prototype changes. The source is GPL-2.0-or-later; the Rust adaptation
is distributed under GPL-3.0-only alongside WayVR. It reuses the normalized pinch
thresholds, debounce, neutral rearming, interaction zone, shoulder ray and
smoothing. KWin's gaze-priority policy is not used: WayVR uses independent hand
pointers and its existing window interaction pipeline.
