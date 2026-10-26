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

The recovery helper requires an active graphical session. It retains the last
reported native/nested backend. A cold launch defaults to nested mode and
requires a host display. A recovered native login keeps the desktop's own seat
wrapper preferentially; a user unit is the fallback. `WIVRN_DOME_EXECUTABLE` can
select an absolute compositor executable. The helper explicitly selects the
installed WiVRn-Pro runtime for a newly launched XR helper; an already-running
DomeD keeps its existing runtime configuration. Its XR session must use the same
headset/runtime as WayVR for aligned head-relative input.

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
