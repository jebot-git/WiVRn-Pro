# Build and install WiVRn Pro

Build both peers from this fork. The Android package is `org.meumeu.wivrn.spatial`.
No Godot runtime is required anywhere. Build helpers do not deploy, launch apps,
connect to a headset or change running services.

## PC

Requires CMake 3.28+, Ninja, a C++23 compiler, Vulkan headers 1.4.304+, OpenXR
headers, Boost (locale/url/iostreams), OpenSSL, Eigen3, nlohmann-json, CLI11,
Avahi, GLib, libnotify, librsvg, libarchive, libdrm, FFmpeg development libraries,
shader tools, libudev and OpenXR loader development libraries, Python 3.11+ and Rust/Cargo. Separate Cargo.lock files pin Shora and the bundled upstream WayVR worker.
WayVR additionally needs libinput, ALSA, fontconfig, PipeWire/SPA, Wayland, X11/XCB
and xkbcommon (including X11) development libraries. Its GLSL shaders use shaderc;
Cargo can build shaderc from source. Use a current Rust toolchain supporting the
pinned workspace dependencies.
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
`WIVRN_SHORA_OFFLINE=ON` and `WIVRN_WAYVR_OFFLINE=ON` use only cached Cargo dependencies for each component. At least one of VAAPI,
Vulkan Video and QSV must be enabled. NVENC/x264/Lighthouse ON is a configure error.

Artifacts: `build/server/server/wivrn-pro-server`,
`build/server/openxr_wivrn-dev.json`, the corresponding runtime shared libraries,
and `build/server/server/wivrn-pro*.service`.

The build helper includes the unified Qt6 dashboard by default. Install
`dashboard/shora/requirements.txt` (PySide6, including QtDBus) in the Python
environment used for configuration; no KDE/Kirigami development packages are
needed. Launch the installed `wivrn-pro-dashboard`. For a server-only build,
pass `-DWIVRN_BUILD_DASHBOARD=OFF`. The dashboard controls `wivrn-pro.service`;
closing it leaves streaming and tracking running. WiVRn's D-Bus name and network
port remain shared with stock WiVRn, so run only one server at a time.

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

The native Shora backend is bundled in the server binary and supervised by WiVRn.
WayVR is compiled from pinned upstream source into a separate worker, installed
at `<prefix>/libexec/wivrn-pro/wayvr`. It is an integral build dependency, uses
OpenXR/Wayland/X11 without OpenVR or Whisper, and does not use an AppImage.
The matching IPC tool is installed as `bin/wivrn-pro-wayvrctl`. PC audio uses
PipeWire by default. Its separate Cargo workspace avoids mixing Shora's and WayVR's OpenXR loader ABIs.
`WIVRN_WAYVR_TARGET_DIR` optionally selects its Cargo cache directory.
No separate Shora executable, profile initializer or service is required. Existing
user configurations are imported once; see [carryover rules](../fork/SHORA.md).

After reviewing the build, installation and activation are explicit operations:

```sh
cmake --install build/server
mkdir -p "$HOME/.config/systemd/user" "$HOME/.local/bin" \
  "$HOME/.local/share/applications" "$HOME/.local/share/icons/hicolor/scalable/apps"
for unit in wivrn-pro wivrn-pro-dashboard; do
  ln -sfn "$HOME/.local/opt/wivrn-pro/lib/systemd/user/$unit.service" \
    "$HOME/.config/systemd/user/$unit.service"
done
for app in wivrn-pro-server wivrn-pro-dashboard wivrn-pro-spatial wivrn-pro-wayvrctl; do
  ln -sfn "$HOME/.local/opt/wivrn-pro/bin/$app" "$HOME/.local/bin/$app"
done
ln -sfn "$HOME/.local/opt/wivrn-pro/share/applications/wivrn-pro.desktop" \
  "$HOME/.local/share/applications/wivrn-pro.desktop"
ln -sfn "$HOME/.local/opt/wivrn-pro/share/icons/hicolor/scalable/apps/wivrn-pro.svg" \
  "$HOME/.local/share/icons/hicolor/scalable/apps/wivrn-pro.svg"
systemctl --user daemon-reload
loginctl enable-linger "$USER"
systemctl --user enable --now wivrn-pro.service
systemctl --user enable wivrn-pro-dashboard.service
# Start this separately only when the graphical session is already running:
systemctl --user start wivrn-pro-dashboard.service
```

Adjust paths if configured with another prefix. The service conflicts with
`wivrn.service`, the old `wivrn-pro-shora.service`, `shora-server.service` and
`shora-proxy.service`, `spatial-bridge.service`, `shora-wayvr.service`,
`wayvr.service` and `wlx-overlay-s.service` to prevent competing owners. Starting it does not
disable those units permanently. Stop their boot activation separately when
making this fork the default. Stopping WiVRn also stops its workers and their child
processes. The core service survives logout and starts at boot when user lingering
is enabled.
The separate attach-only desktop dashboard starts with `graphical-session.target`
and stops at logout. No separate Shora, spatial or WayVR service is needed.
WayVR and its in-headset dashboard start when the Quest creates a VR session.
The supervisor refreshes the desktop display environment from the user manager
at each WayVR launch, so a server started before login can capture the desktop
after login. A graphical session is required for desktop capture; boot activation
does not enable automatic desktop login or turn on the Quest.
The desktop session must export its display/session environment to the user
service manager (normally done by the desktop). Desktop capture also requires
its working PipeWire/portal services.

The fork runtime manifest is `<prefix>/share/openxr/1/openxr_wivrn_pro.json`.
Both internal workers use it explicitly. WiVRn retains upstream's runtime-selection
behavior for VR applications when started. Builds and offline tests do not select
a runtime or activate an installed service.

For a review-only install use `DESTDIR=/absolute/staging cmake --install build/server`.
Validate generated units with `systemd-analyze --user verify`; this does not start them.
