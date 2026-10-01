# Build and install WiVRn Pro

Build both peers from this fork. The Android package is `org.meumeu.wivrn.spatial`.
No Godot runtime is required anywhere. Build helpers do not deploy, launch apps,
connect to a headset or change running services.

## PC

Requires CMake 3.28+, Ninja, a C++23 compiler, Vulkan headers 1.4.304+, OpenXR
headers, Boost (locale/url/iostreams), OpenSSL, Eigen3, nlohmann-json, CLI11,
Avahi, GLib, libnotify, librsvg, libarchive, libdrm, FFmpeg development libraries,
shader tools, Python 3 and Rust/Cargo. Cargo.lock pins the embedded Shora dependencies.
Monado's revision and patches remain managed by WiVRn CMake. See CI for an
Ubuntu dependency list. Native installation is supported; old distro/container
release recipes are deliberately absent.

```sh
fork/build-server.sh -DCMAKE_INSTALL_PREFIX="$HOME/.local/opt/wivrn-pro" \
  -DCMAKE_BUILD_TYPE=RelWithDebInfo
# Reconfigure with QSV when wanted:
fork/build-server.sh -DWIVRN_USE_QSV=ON
```

QSV defaults OFF. Enabling it checks FFmpeg for `h264_qsv`, `hevc_qsv` and a QSV
hardware context without opening a GPU. Runtime also needs a compatible Intel
media driver and oneVPL dispatcher/implementation. Encoding uses VAAPI surfaces
on the same Arc as Vulkan. An explicit `qsv` selection fails if QSV is unavailable;
it never silently selects a different backend. See [configuration](configuration.md).
`WIVRN_SHORA_OFFLINE=ON` uses only cached Cargo crates. At least one of VAAPI,
Vulkan Video and QSV must be enabled. NVENC/x264/Lighthouse ON is a configure error.

Artifacts: `build/server/server/wivrn-pro-server`,
`build/server/openxr_wivrn-dev.json`, the corresponding runtime shared libraries,
and `build/server/server/wivrn-pro*.service`.

For the optional Qt6/Kirigami dashboard, add `-DWIVRN_BUILD_DASHBOARD=ON` and build
`wivrn-dashboard`. It uses the fork server next to its executable. USB installer
and connection controls have been removed. The dashboard is not needed for the
systemd service. WiVRn's D-Bus interface and network port remain shared with
stock WiVRn, so run only one server at a time.

## Quest Pro APK

Requires JDK 21, Android SDK 34, NDK 29.0.14206865, Android CMake 3.31.5,
`glslangValidator`, `spirv-opt`, `rsvg-convert`, gettext and KTX-Software's `ktx`.
Set `JAVA_HOME` and `ANDROID_HOME`, then:

```sh
fork/build-client.sh
```

The debug APK is under `build/android/outputs/apk/debug/` and is debug-signed.
For cached builds pass `--offline -Pwivrn_offline=1
-Pfetchcontent_base_dir=/absolute/cache/path` on the same command line.
Release signing follows the Gradle signing configuration; keep keys untracked.
No store release or installation is performed by the build.

## User services

The Shora tracking backend must already be installed. Defaults are
`/usr/bin/slimevr-server-rs` and `/usr/libexec/slimevr-server-rs/init-profile`;
override `WIVRN_SHORA_BACKEND` and `WIVRN_SHORA_INIT` at configure time if needed.
It retains the user's existing `~/.config/shora-rust/server.toml` profile.

After reviewing the build, installation and activation are explicit operations:

```sh
cmake --install build/server
mkdir -p "$HOME/.config/systemd/user"
ln -sfn "$HOME/.local/opt/wivrn-pro/lib/systemd/user/wivrn-pro.service" \
  "$HOME/.config/systemd/user/wivrn-pro.service"
ln -sfn "$HOME/.local/opt/wivrn-pro/lib/systemd/user/wivrn-pro-shora.service" \
  "$HOME/.config/systemd/user/wivrn-pro-shora.service"
systemctl --user daemon-reload
systemctl --user enable --now wivrn-pro.service
```

Adjust paths if configured with another prefix. Starting the fork conflicts
with `wivrn.service`; its backend conflicts with `shora-server.service` and
`shora-proxy.service`. This prevents two tracking backends from competing for
trackers. It does not disable those units permanently. Stop their boot activation
separately if making this the default installation. Stop both fork units to end
both processes; restarting the backend alone leaves WiVRn and its bridge running.

The fork runtime manifest is `<prefix>/share/openxr/1/openxr_wivrn_pro.json`.
Shora's unit uses it explicitly. WiVRn retains upstream's runtime selection
behavior for VR applications when the service is started. No runtime selection
or service activation is performed while building or testing offline.

For a review-only install use `DESTDIR=/absolute/staging cmake --install build/server`.
Validate generated units with `systemd-analyze --user verify`; this does not start them.
