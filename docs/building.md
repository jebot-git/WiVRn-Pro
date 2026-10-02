# Build and install WiVRn Pro

Build both peers from this fork. The Android package is `org.meumeu.wivrn.spatial`.
No Godot runtime is required anywhere. Build helpers do not deploy, launch apps,
connect to a headset or change running services.

## PC

Requires CMake 3.28+, Ninja, a C++23 compiler, Vulkan headers 1.4.304+, OpenXR
headers, Boost (locale/url/iostreams), OpenSSL, Eigen3, nlohmann-json, CLI11,
Avahi, GLib, libnotify, librsvg, libarchive, libdrm, FFmpeg development libraries,
shader tools, libudev and OpenXR loader development libraries, Python 3.11+ and Rust/Cargo. Cargo.lock pins the embedded Shora dependencies.
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
No separate Shora executable, profile initializer or service is required. Existing
user configurations are imported once; see [carryover rules](../fork/SHORA.md).

After reviewing the build, installation and activation are explicit operations:

```sh
cmake --install build/server
mkdir -p "$HOME/.config/systemd/user"
ln -sfn "$HOME/.local/opt/wivrn-pro/lib/systemd/user/wivrn-pro.service" \
  "$HOME/.config/systemd/user/wivrn-pro.service"
systemctl --user daemon-reload
systemctl --user enable --now wivrn-pro.service
```

Adjust paths if configured with another prefix. The service conflicts with
`wivrn.service`, the old `wivrn-pro-shora.service`, `shora-server.service` and
`shora-proxy.service` to prevent competing tracker owners. Starting it does not
disable those units permanently. Stop their boot activation separately when
making this fork the default. Stopping WiVRn also stops its tracking worker.

The fork runtime manifest is `<prefix>/share/openxr/1/openxr_wivrn_pro.json`.
The internal worker uses it explicitly. WiVRn retains upstream's runtime-selection
behavior for VR applications when started. Builds and offline tests do not select
a runtime or activate an installed service.

For a review-only install use `DESTDIR=/absolute/staging cmake --install build/server`.
Validate generated units with `systemd-analyze --user verify`; this does not start them.
