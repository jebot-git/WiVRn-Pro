# Optional DomeD / de-xr dashboard

Select `WIVRN_DASHBOARD_FRONTEND=fltk` with `WIVRN_BUILD_DASHBOARD=ON`
when building WiVRn-Pro. The default remains `qt`. Both choices install the same
`wivrn-pro-dashboard` command and desktop/user-service integration; only the
selected frontend is installed. This native C++ frontend does not load Qt.
Python 3.11+ is used solely for asynchronous TOML validation and serialization.

The first implementation prioritizes live skeleton rendering, service ownership,
and the existing WiVRn D-Bus / Shora control protocols. It retains the five-tab
organization. Some advanced controls use editable JSON rather than individual Qt
widgets; exact Qt presentation and complete UI parity are not claimed.

## Build without rebuilding the VR server

Use DomeD's installed **patched shared FLTK 1.4.5**, GIO / gio-unix, Fontconfig,
zlib, nlohmann-json, Python 3.11+, CMake 3.28+ and a C++17 compiler. The DomeD source
checkout supplies `native/gpu-window.h`, `fltk_appearance.h` and their helper
headers; a stock FLTK installation is not a substitute for the de-xr patches.

```sh
DOME_SOURCE="$HOME/de-xr/dome-compositor" \
DOME_FLTK_PREFIX=/opt/fltk-vr/toolkit \
./fork/build-dashboard-fltk.sh
```

Equivalent standalone configuration:

```sh
cmake -S dashboard -B build-dashboard-fltk -G Ninja \
  -DWIVRN_DASHBOARD_FRONTEND=fltk \
  -DCMAKE_PREFIX_PATH=/opt/fltk-vr/toolkit \
  -DWIVRN_DOME_SOURCE="$HOME/de-xr/dome-compositor"
cmake --build build-dashboard-fltk
build-dashboard-fltk/fltk/wivrn-pro-dashboard --show
```

For a full server build, pass the same three `-D` options to
`fork/build-server.sh`, then build the `wivrn-dashboard` target. Install with the
chosen prefix using CMake's `Dashboard` component. This does not replace or
restart the streaming runtime.

## DomeD session and tray manager

In a DomeD session (`XDG_CURRENT_DESKTOP` contains `Dome`), startup reserves the
same `io.github.wivrn.ProDashboard` single-instance name used by the Qt frontend,
exports a standard StatusNotifier item with ID **`wivrn.pro`**, and leaves its
window hidden. The existing user unit and desktop launcher keep working.
`--show` explicitly opens the window; `--tray` requests hidden startup elsewhere.
A second invocation activates the existing window. Desktop and WayVR instances
keep the existing rule that an activation from another surface does not move or
reveal the window there. WayVR starts with a visible window and no desktop tray.

DomeD must include the companion change in its compositor and native tray
manager: `wivrn.pro` defaults to managed placement, alongside `dome.translator`.
No dashboard code edits the user's placement configuration. **Move to tray** /
**Move to manager** overrides remain respected. Until those DomeD components are
rebuilt, the dashboard still works as a normal standalone tray application.
If the tray host is unavailable, the dashboard opens a reachable fallback window.
Closing hides to the tray when available; Quit confirms unsaved edits and leaves
WiVRn running.

## Functional coverage

- Authoritative bone endpoints, tracked/inferred coloring, HMD-relative centering,
  floor grid, depth ordering, front/side/orbit presets, drag orbit, wheel zoom and
  double-click reset. Invalid/nonfinite bone coordinates are rejected.
- Guided standing, yaw and mounting resets; all relaxed-pose captures and walking
  calibration; countdown cancellation on tracking/reference loss.
- Live tracking settings, walking/body-turn tuning, Stay Aligned, drift clearing,
  active tuning export and advanced JSON settings. Failed settings commands retain
  drafts. Successful changes use the worker's authoritative response.
- Asynchronous WiVRn D-Bus property polling, configuration save with re-read conflict
  detection, encoder presets, pairing, key rename/revoke, disconnect and WayVR restart.
  Raw JSON preserves unknown settings and custom encoder layouts.
- Asynchronous `systemctl --user start/stop/restart wivrn-pro.service`, duplicate
  action prevention, captured error output and a 20-second client deadline.
  Closing the dashboard never terminates the service. Service logs can be loaded
  asynchronously from the Server log tab.
- Bounded, serialized Shora commands over its Unix socket, 4-second deadlines,
  fragmented-response handling, 1 MiB response limit and automatic reconnect.
- Startup profile JSON editing, common HaritoraX and OSC controls, tracker status
  listing, fresh-readback-gated hardware settings, Motoc configuration and actions.
  Profile writes validate TOML, detect conflicts, replace atomically and use mode 0600.
- DomeD's live palette/accent/font selection via the shared appearance helper.
  No application/global menu integration is required.

The backend uses a GLib context serviced by an FLTK timer, including while modal
FLTK dialogs are open. It never performs service operations on the rendering path.
The window redraws all children after clearing its GPU framebuffer so live
skeleton updates cannot erase other controls.

## Validation

```sh
cmake -S dashboard -B build-dashboard-fltk \
  -DWIVRN_DASHBOARD_FRONTEND=fltk \
  -DCMAKE_PREFIX_PATH=/opt/fltk-vr/toolkit \
  -DWIVRN_DOME_SOURCE="$HOME/de-xr/dome-compositor" \
  -DWIVRN_DASHBOARD_FLTK_TESTS=ON
cmake --build build-dashboard-fltk
python3 dashboard/fltk/tests/integration.py build-dashboard-fltk
```

The test needs Python GObject introspection, Pillow, `dbus-run-session`, `Xvfb`
and `xwininfo`. It creates a private session bus and forces X11 on its private
Xvfb display; live WiVRn services and the host Wayland session are not used.
The native driver exercises state/save/conflict handling, calibration/settings,
service success/failure, fragmented/malformed/oversized replies, timeout and
reconnect. The real-window test checks hidden startup, tray menu, cross-process
activation and pixel output. Profile tests cover round-trip, permissions,
conflict and invalid TOML handling. To exercise DomeD's actual tray-manager
placement and menu code too, set `DOME_TRAY_DRIVER` to its built
`build/tray/tray-service-driver`.

Diagnostic arguments: `--socket PATH`, `--profile PATH`, `--page skeleton`,
`--snapshot JSON`, `--screenshot FILE.ppm` and `--exit-after SECONDS`.
`--snapshot` is a preview fixture; normal operation always uses live Shora data.
Set `DOME_COMPOSITOR` to a built DomeD compositor to additionally run the dashboard
as a Wayland-only client inside an isolated nested DomeD instance. The test
requires a mapped WiVRn surface and captures `fltk-dome-wayland.png`.

Physical headset interaction and complete Qt feature parity remain separate
validation work.
