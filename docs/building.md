# Build and install WiVRn Pro

Build both peers from this fork. The Android package is `org.meumeu.wivrn.spatial`.
No Godot runtime is required anywhere. Build helpers do not deploy, launch apps,
connect to a headset or change running services.

## PC

Requires CMake 3.28+, Ninja, a C++23 compiler, Vulkan headers 1.4.304+, OpenXR
headers, Boost (locale/url/iostreams), OpenSSL, Eigen3, nlohmann-json, CLI11,
Avahi, GLib, libnotify, librsvg, libarchive, libdrm, FFmpeg development libraries,
shader tools, libudev and OpenXR loader development libraries, Python 3.11+ and Rust/Cargo. Separate Cargo.lock files pin Shora, WayVR and Motoc.
WayVR additionally needs libinput, ALSA, fontconfig, PipeWire/SPA, Wayland, X11/XCB
and xkbcommon (including X11) development libraries. Its GLSL shaders use shaderc;
Cargo can build shaderc from source. PipeWire bindings need libclang and its C
resource headers. Use Rust 1.95 or newer for the pinned workspace dependencies.
Monado's revision and patches remain managed by WiVRn CMake. See CI for an
Ubuntu dependency list. Native installation is supported. RPM, Debian 13 and
Arch packaging recipes are documented in [native portability packages](../fork/packaging/README.md).

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
`WIVRN_SHORA_OFFLINE=ON`, `WIVRN_MOTOC_OFFLINE=ON` and `WIVRN_WAYVR_OFFLINE=ON` use only cached Cargo dependencies for each component. At least one of VAAPI,
Vulkan Video, QSV and NVENC must be enabled. NVENC and Lighthouse/Motoc build support default ON;
use `WIVRN_USE_NVENC=OFF` or `WIVRN_FEATURE_STEAMVR_LIGHTHOUSE=OFF` to omit them.
Lighthouse mode needs a compatible SteamVR Lighthouse driver installation and device USB permissions.
Selecting xrizer for OpenVR does not supply that hardware driver. x264 remains unsupported.

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

Requires JDK 17 or 21, Android SDK 34, NDK 29.0.14206865, Android CMake 3.31.5,
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

### Host checks and system runtime

Run `wivrn-pro-check` as the streaming user. It loads the installed runtime,
opens DRM render nodes, verifies a hardware Vulkan device without relying on a
desktop display, connects to PipeWire, checks WirePlumber, and opens `/dev/uinput`
without creating any input devices. GPU encoding capability is still validated by
the selected encoder when a headset connects. Install `vulkaninfo` (usually the
`vulkan-tools` package), PipeWire's `pw-cli`, and WirePlumber for these checks.
The core user service requests PipeWire's socket and WirePlumber and records
these checks in its journal at startup. Missing optional host facilities do not
prevent the streaming service from starting; the standalone checker exits nonzero
when full functionality is unavailable.

For a user-prefix installation, install a system-visible copy of the matching
runtime and persistent pre-login input permissions with:

```sh
sudo "$HOME/.local/opt/wivrn-pro/bin/wivrn-pro-install-system" --user "$USER"
```

This installs a root-owned manifest at
`/usr/local/share/openxr/1/openxr_wivrn_pro.json` and copies both runtime libraries
into `/usr/local/lib/wivrn-pro`. It does not select a global active runtime. It
loads `uinput` at boot, assigns `/dev/uinput` to the dedicated `wivrn-pro-input`
group, adds the selected user to that group and the existing `render` group, and
enables lingering. It does not grant access to all physical input devices.
Reboot for the lingering user manager to acquire new group membership; replacing
groups in a running desktop would terminate its services. Enable the user's
`wivrn-pro.service` as above. Re-run this installer after updating a user-prefix
runtime; `wivrn-pro-check --system` checks ownership, loadability and matching
library hashes. Startup checks also verify the system copy when installed.
`--prefix PATH` selects another installed prefix;
`--destdir PATH` stages files without changing the host, accounts or services.

### LightXR display manager

With the single-user LightXR/DomeD login flow, leave WiVRn-Pro in its lingering
user service. The server queries the system manager's `display-manager.service`
identity on headset connection and helper restart, with the selected unit as an
early-boot fallback. When it is `lightxr.service`, LightXR/DomeD owns the session
lifecycle: managed WayVR, its in-headset dashboard, configured application
autostart and automatic desktop-dashboard startup are suppressed. WiVRn's
streaming runtime and tracking continue through login and logout. Settings are
preserved, and manually launching the desktop dashboard remains possible.
Other display managers retain the existing configured startup behavior.

The installed dashboard unit uses `wivrn-pro-server --desktop-autostart-allowed`
as an `ExecCondition`; this read-only query exits 1 for LightXR and 0 otherwise.
No LightXR daemon needs to invoke a privileged WiVRn command or restart the
runtime at login. Configure LightXR's broker to allow the same user and its
system-installed WiVRn-Pro manifest. LightXR installation/PAM configuration and
selecting it as the display manager remain separate deployment operations.
Cold-boot connection, greeter controller focus, authentication, DomeD attachment,
and return to the greeter require validation on the actual headset.

For a review-only install use `DESTDIR=/absolute/staging cmake --install build/server`.
Validate generated units with `systemd-analyze --user verify`; this does not start them.
