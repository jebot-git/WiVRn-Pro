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

The independently installed Shora tracking backend is not copied into this
repository. See [integration boundaries](SHORA.md). OpenXR, Monado, FFmpeg,
Vulkan and other dependency notices remain applicable to binary distribution.
