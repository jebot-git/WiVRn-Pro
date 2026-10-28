# DomeD integration

Build and install the WiVRn-Pro server (including bundled WayVR) and the matching
DomeD compositor. No headset APK or global OpenXR runtime change is required.

When DomeD's version-one D-Bus interface becomes ready, WayVR hides its screen
and mirror overlays and their desktop selection list. The wrist watch instead
shows **XR / desktop**, **Launch**, **XR screenshot**, and **Terminal**. Existing
mirror configuration is retained and resumes if DomeD disappears. The ordinary
Input fan includes **WiVRn hand tracking**, separately from virtual D-pads and
hands-as-controllers. This toggle controls native application joint delivery;
WayVR retains joints so the watch can re-enable it. DomeD retains native joints
in hands-as-controllers mode when hand tracking is enabled. Virtual pads keep
their existing axes, gestures, ownership and timeout behavior.

## Recovery and explicit stops

The server checks DomeD asynchronously once per second. A missing compositor
gets a three-second recovery grace period, then the independent user unit
`wivrn-pro-dome.service` is started with bounded retry backoff. Stopping WiVRn
does not stop this unit. DomeD's native login wrapper recovers crashes in the
same login/seat context while WiVRn is active. A native singleton lock prevents
simultaneous recovery processes from claiming the DRM seat.

An explicit DomeD stop (logout, closing the nested compositor, SIGTERM/INT/HUP,
or D-Bus `Activate("stop")`) writes `$XDG_RUNTIME_DIR/dome/explicit-stop`.
Both recovery paths honor it. Starting DomeD manually clears it once the new
compositor owns its bus name. `wivrn-pro-dome --resume` also clears it and starts
the recovery unit. SIGKILL, a crash, or losing the D-Bus owner do not set it.

The recovery helper is a background policy process. It never automatically opens
an XR/nested window on the desktop, including when the cached backend is nested
or unknown. It requires an active graphical session whose current
`XDG_SESSION_DESKTOP` is `dome` and a version-one context from a previously
observed native (`drm`) compositor. A different login, missing context or a
failed D-Bus query leaves the current session alone. To start a nested compositor,
launch it explicitly; `--resume` permits recovery but does not override these
background-launch rules.

Both the supervisor and launcher check the native compositor's existing
`dome/native-owner.lock` without creating/removing it. A held lock is authoritative
even when the optional `org.domed.Compositor` bus name is unavailable. This
prevents a second nested compositor from briefly mapping/focusing a window before
failing on the existing control port. A second lock check immediately before
exec allows the login wrapper to win recovery races; the compositor retains its
own native singleton protection.

The supervisor caches both `xr_active` and `shown` with the backend. Native
fallback starts XR only when both were explicitly true: a selected desktop or
hidden XR presentation stays in desktop mode, and older context defaults to no
XR startup. Recovery never sends an activation/focus/toggle command to a running
compositor. A nested diagnostic owner cannot replace the cached native context
while the native seat lock is held.

`WIVRN_DOME_EXECUTABLE` can select an absolute compositor executable. The helper
selects the installed WiVRn-Pro runtime only for a newly recovered XR helper;
an already-running DomeD keeps its existing runtime configuration. No global
OpenXR runtime is changed.

## Notifications and input

Internal and UDP WayVR notices go directly to DomeD, using a delivery ID shared
with its standard notification service. Retries and dismissal do not create
another popup/history entry. WayVR stops mirroring desktop notifications only
when DomeD owns `org.freedesktop.Notifications`; a nested DomeD does not replace
the host desktop's notification daemon. Queues and payloads are bounded.

WayVR sends head-relative rays and L/M/R button/scroll states from one D-Bus
worker; the rendering thread never waits on D-Bus. DomeD raycasts its actual
flat/curved windows and shell, and dispatches through its normal Wayland seat.
A nearer WayVR overlay, the watch fan, virtual D-pad ownership, Hands Frame,
playspace drag, or an existing WayVR grab keeps the gesture local. Emulated game
controllers are reserved while the hand controls DomeD. Physical mouse activity
has priority; idle handoff waits 350 ms. Input leases expire after 200 ms and
release held buttons. Tracking loss, realm changes, and handoff require a neutral
gesture before another press. Frame sequence/timestamp validation and bounded
queues prevent stale input replay.

The interface and verification commands are documented in DomeD's
`docs/WIVRN.md`. No real desktop or connected headset session is restarted by
the tests.

## Local validation (2026-10-26)

The complete server/WayVR build with `WIVRN_DASHBOARD_FRONTEND=fltk` succeeds.
The FLTK dashboard's four private-bus tests pass, including its real skeleton
window, settings/service backend, tray manager and native DomeD Wayland mapping.
The executable links to the patched shared FLTK 1.4.5 libraries, with no Qt
libraries. DomeD's full `multi-gpu` Cargo suite, private compositor pointer/control
integration, four login recovery cases, WiVRn's asynchronous supervisor/launcher
checks, WVJ3/virtual-pad driver checks, watch geometry model and two bounded
WayVR queue tests pass. Source-pin/patch and fork policy audits pass.

The broader legacy offline script cannot finish on this machine: its Qt dashboard
tests require the unavailable `PySide6.QtDBus` module, and its unrelated passthrough
lifecycle test requires spdlog development headers. These dependencies are not
required for the FLTK dashboard. Physical headset QA is still outstanding.

## Background recovery regression checks (2026-10-28)

Ten launcher tests cover native desktop/XR/hidden choice, strict context/session
checks, refusal to create nested windows, held/stale/unsafe ownership locks,
D-Bus failure/timeout, explicit stop/resume and an owner appearing during the
launch decision. The private-bus C++ supervisor test verifies mode updates,
refusal to overwrite native context from a nested owner, no unit start after
D-Bus-name loss while the native lock remains held, and ordinary recovery after
that lock is released. These tests create no real graphical windows or focus
changes. The optimized Release server build also passes.

## System-wide installation

Configure optimized server and FLTK dashboard builds with
`-DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr/local`; the prefix must be
set at configure time because the server embeds its WayVR/helper paths. Keep
`WIVRN_DASHBOARD_FRONTEND=fltk`. Build as the regular user, then stage and verify
both builds before installing system files:

```sh
DESTDIR="$PWD/build/system-stage" cmake --install build/server
DESTDIR="$PWD/build/system-stage" cmake --install build/dashboard-fltk
python3 fork/install-service-overrides.py --prefix /usr/local --destdir build/system-stage
```

Install the staged files as root using the normal package/deployment workflow.
`fork/install-service-overrides.py --prefix /usr/local` can also install just the
three system service overrides with sudo after a normal CMake installation.
They live under `/etc/systemd/user/` and ensure legacy per-user service symlinks
also use the root-owned server, background helper, host checker and FLTK
dashboard. They do not enable services for additional users, change device
permissions, select the global OpenXR runtime or restart any process.

Run `systemctl --user daemon-reload` as the desktop user after installation.
The new launcher applies to subsequent watchdog requests immediately. An already
running WiVRn server retains its loaded code until its next normal restart;
installation does not disconnect the headset or restart DomeD/display sessions.
