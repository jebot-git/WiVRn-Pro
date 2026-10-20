# Spatial bridge inside WiVRn

This is the `wivrn-native` branch. Room capture runs in the WiVRn Quest client;
localization, transport, stage alignment and PC subscriptions use the same WiVRn
session. The standalone implementation remains on `main`. This branch is a root-level
WiVRn fork with capture in `client/` and the PC bridge in `server/driver/`.

No Godot runtime, separate Quest capture application, Python broker, or controller
calibration is required for the native path. Python is only used by the optional
subscriber example. This branch changes WiVRn's protocol: **build both peers from
this branch**, rather than mixing it with stock WiVRn or the old anchor prototype.

## Client behavior

Open **Room capture** in the Quest WiVRn lobby to capture or check markers before
connecting to a PC. During a stream, the same controls are available in WiVRn's
window (both thumbsticks), under **Settings → Room capture**.
**Start / recheck 12 markers** temporarily enables passthrough; in a stream it also
hides video while keeping the connection and feedback running. The lobby defers
automatic connections while this page is open and defers entering a ready stream
while capture is active. Finish capture before connecting. The guidance panel follows
your head. Turn through four directions, hold steady for three markers in each,
and complete the reload verification pass. **Retry / recheck**, **Skip**, and
**Finish room capture** (lobby) / **Return to streaming** are panel buttons. Marker axes use WiVRn's Vulkan renderer
and share one projection layer with controller rays when the panel is open.
**Show marker axes** keeps a preview visible after capture. **Saved marker helper**
shows each slot's saved UUID, current localization and runtime result; **Reload**
retries that saved identity without creating a replacement. It is disabled during
capture or a pending operation; failed localization can be retried.

The workflow requires tracking, stable head/anchor poses, and successful local
anchor persistence. Reload verification rejects shifts above 3 cm or 3 degrees.
**12 is the configured budget, not a claimed Quest hardware limit or a new
hardware-validated maximum.** Partial capture remains explicitly partial. This
native UI has not yet been tested in a headset.

The application owns anchors for its entire OpenXR session, independently of the
lobby/stream scenes. Pending asynchronous requests, localized anchors, capture
progress and room entities survive stream disconnects/reconnects. Events are
forwarded once centrally; scene teardown only destroys visual marker nodes.
Saved UUIDs auto-load again on the next application launch. Streaming publishes
snapshots at five per second; the lobby localizes without publishing to a PC. WiVRn remains the foreground XR application: there is no second Quest
background service and no cross-app SLAM alignment. Loss of focus/tracking and
pending reference-space changes invalidate published poses and reset capture
dwell. Saved UUIDs are in WiVRn's private `spatial-bridge-v1/` directory.

**Allow room scene access** requests the Quest scene permission. Existing room
entities are queried from the runtime; this does not run Meta Room Setup. Export
is bounded at 64 entities, 128 bytes of labels each, and 32 boundary vertices per
entity. Oversized boundaries are omitted and flagged, not silently simplified.
Available boxes and semantic labels remain. **Reload room scene** refreshes a
completed query. STAGE bounds are a rectangle, never a Guardian polygon.

The APK is **`org.meumeu.wivrn.spatial`**, with its own saved anchors and permissions.
Anchors from the standalone capture app do not automatically migrate across app
identities. A fresh native capture will be needed when hardware testing resumes.

## Server alignment and PC clients

The native API is integral and starts with the WiVRn runtime session and listens at:

```
$XDG_RUNTIME_DIR/wivrn-pro/bridge.sock
```

The fallback runtime directory is `/run/user/UID`. The bridge creates its own
0700 directory and 0600 Unix `SOCK_SEQPACKET` socket, checks peer UID, and holds an
exclusive lock against a second publisher. It supports **32 simultaneous PC
readers of one WiVRn headset session**. This does not add simultaneous HMD streams
to WiVRn. Slow readers are disconnected; they cannot block XR or other readers.
There is no command or remote capture interface on this socket.

Snapshots travel through WiVRn's reliable control channel and use WiVRn clock
synchronization. For each snapshot, the server composes:

```
PC_STAGE_from_Quest_STAGE = PC_STAGE_from_runtime_root × tracking_origin_offset
```

The runtime spaces are located at the mapped sample timestamp. This uses the
same tracking origin as WiVRn's HMD and automatically includes runtime offsets;
it does not assume two applications have identical STAGE origins. Alignment is
unavailable until clock synchronization and the runtime spaces are valid.

The JSON schema is `wivrn.spatial.v1` (separate from the standalone TCP protocol).
One socket record contains one JSON object, at most 128 KiB. Important fields:

| Field | Meaning |
| --- | --- |
| `live`, `status` | Whether this snapshot can provide current poses |
| `source_session`, `sequence`, `generation` | Client OpenXR-session epoch, monotonic sequence, reference-space generation |
| `sample_monotonic_ns`, `ttl_ns`, `age_ns` | PC `CLOCK_MONOTONIC` sample time; 750 ms pose TTL |
| `alignment` | Pose of Quest STAGE in PC STAGE; null when unavailable |
| `markers[].pose`, `scene.entities[].pose`, `head` | `valid`, `flags`, raw `quest_stage`, aligned `pc_stage` |
| `capture` | Anchor support, capture activity, accepted and verified counts |
| `scene.entities[].boundary_2d`, `box` | Geometry in that entity's local coordinates |
| `boundary` | Quest STAGE rectangle; apply `alignment` to place it on PC |

Positions are metres and quaternion order is `[x,y,z,w]`. Use the full rigid
transform, including orientation, for geometry. The PC coordinate system is the
runtime's semantic STAGE; applications using LOCAL must apply their own
STAGE-to-LOCAL transform.

Clients must check `live`, each pose's `valid`, and freshness **when consuming**
the record, including time spent in their receive queue. Accept no more than
100 ms of future prediction and expire after 750 ms. The server clears all poses
on disconnect, expiry, or unavailable alignment, while retaining geometry and
UUID metadata as explicitly non-live data in memory. It sends idle heartbeats;
it does not persist a live PC pose or calibrated transform across sessions.

A dependency-free reader example reconnects automatically:

```sh
python3 fork/subscribe.py
python3 fork/subscribe.py --once
```

## Build and service integration

See [build/service setup](../docs/building.md). The native spatial API and Shora
bridge run inside `wivrn-pro-server`; neither needs a Python broker or a separate
bridge service. The same service supervises the bundled upstream WayVR overlay
worker and can open this fork's Qt dashboard in a virtual Wayland display; see
[worker configuration](../docs/configuration.md#wayvr-worker). The reader example is also installed as `wivrn-pro-spatial`.

## Room-fixed body calibration guide

In the Quest spatial settings, open **Body tracking calibration guide** after
localizing at least one saved room marker. Stand upright in a clear location,
face the preferred reset direction, and select **Save calibration spot here**.
The nearest tracked saved marker owns the standing position and facing direction;
`calibration-spot-v1.txt` stores the marker UUID and relative pose in the existing
private `spatial-bridge-v1` directory. It does not replace marker UUID files.

Enable **Show calibration guide** to display floor axes and a facing target
1.2 metres ahead at the captured head height, in either the lobby or stream.
Stand at the floor axes, face the raised target, align head/torso/feet, and issue
the usual full or yaw reset. The panel reports distance and facing difference.
Nothing resets trackers automatically or treats head direction as limb direction.

The guide resolves its anchor afresh each frame and hides on tracking loss,
reference-change transitions, or missing/replaced anchor identity. It never
reuses a saved world pose when localization fails. On app restart the saved spot
loads, but its overlay remains off until explicitly enabled. Use **Replace
calibration spot here** to deliberately choose a new location/direction. The
helper supplies a repeatable visual cue; physical accuracy and usability still
require the deferred headset tests.

## Offline verification

```sh
fork/test-offline.sh
python3 fork/check-android-protocol.py .cxx/Debug/BUILD_ID/arm64-v8a/compile_commands.json
```

The tests use mocked OpenXR dispatch and isolated temporary Unix sockets. They
cover anchor lifecycle, application-owned capture/late events and saved-marker reloads,
guided scanning/reload verification, rigid transforms,
multiple/late/slow readers, expiry, tracking loss, invalid numbers, replay and
generation handling, maximum geometry, socket ownership, wire round trips and
truncated packets. Actual PC/ARM compiler constants are compared without running
either compiled artifact. See [validation record](VALIDATION.md).

**Live HMD tests remain suspended until explicitly requested.** Offline checks do
not establish capture UX, spatial accuracy, headset performance or scene API
behavior on the device. See [source provenance](PROVENANCE.md).

## Anchored passthrough investigation

[Anchored passthrough windows](ANCHORED-PASSTHROUGH.md) examines Monado keying,
WiVRn's existing alpha transport, compositor aperture masks and a client-side
alternative for ordinary XR apps without MR support. The report also documents the implemented client prototype: **Settings → Room
capture → Anchored passthrough cutout** provides two-hand rectangle design,
trigger/pinch confirmation, enable/disable and deletion. Localize a saved marker
first. The aperture stays attached to that marker and hides during tracking loss;
it cuts through the PC stream while keeping local WiVRn UI visible. Hardware
validation is still outstanding.

## Pin WayVR windows to the room

In WayVR edit mode, open a window's positioning tab and select **Pin to room**.
The nearest stable persisted room marker owns the window's current pose, size and
orientation. **Unpin** removes the binding and keeps the current STAGE placement.
Choosing any ordinary positioning mode also removes the room binding. Leave edit
mode to save through WayVR's normal layout persistence. Pins belong to each
window's saved state in its set; showing a set does not recenter pinned windows.
Dragging a localized pinned window updates its anchor-relative placement.

This requires the WiVRn Quest spatial bridge and at least one localized saved
marker. The reader accepts only fully tracked poses, waits for two seconds of
stable localization, checks session/generation/sequence and the 750 ms sample TTL,
and clears poses on disconnect. Pinned windows are hidden and excluded from
pointer/touch/grab input while localization is unavailable. Missing or replaced
UUIDs never fall back to an absolute saved world pose. A reconnect or reference
change requires settling again. PC playspace changes temporarily hide pins until
a fresh bridge sample supplies the new PC STAGE mapping. The bridge remains at
5 Hz; close physical-surface registration needs headset validation.

Room pins are orthogonal to WayVR's existing movable center marker. They neither
move that marker nor change the game tracking origin. The current consumer starts
only in the OpenXR backend; saved room pins do not become usable SteamVR anchors.

## Remember the calibrated floor

First establish the correct current floor, using the headset's room setup or
WayVR's existing **Fix Floor** controller procedure if needed. Stop playspace
movement, localize a saved room marker and wait for stable tracking. On the watch,
open **Calibration** and select **Remember floor**. This saves a point and orientation on the current
PC STAGE floor relative to a persisted marker in WayVR's `playspace.json5`.
The operation reports success only after the settings file is written. Existing
playspace center settings and old profiles remain compatible.

On a new spatial connection or headset reference-space generation, WayVR waits for
stable raw anchor poses and stable PC placement, then changes Monado's STAGE
translation to bring that saved physical floor back to Y=0. It preserves the stage
rotation and horizontal position in stage coordinates. It applies once per
connection/generation, after a successful runtime write; repeated bridge samples
do not accumulate offsets or continuously undo deliberate playspace dragging.
The saved reset-center height is adjusted in memory to keep reset behavior
consistent. Pin poses wait for a fresh mapping after the floor write.

**Calibration → Forget floor** removes the saved reference and disables subsequent
automatic restoration, without changing the current floor. **Remember floor**
can replace the reference after a deliberate recalibration. The watch reports
whether restoration is off, active, or waiting for stable room localization.
A saved UUID that cannot localize leaves restoration pending. Tilt above five
degrees and automatic height corrections above two metres are rejected; there is
no attempt to infer a physical floor from an arbitrary anchor or from head height.
This restores an established calibration; it does not run room scanning or change
the headset's own boundary/floor settings.

Offline room reader, persistence and transform tests (no headset/compositor):

```sh
cargo test --locked --manifest-path fork/tests/room/Cargo.toml
```

The tests compile the production reader and binding types, including a real Unix
SEQPACKET producer, tracking loss, reconnect/generation changes, stale/replayed
records, scale preservation and floor correction math. The WayVR test target also
checks backward-compatible window and playspace deserialization. Live placement,
relocalization accuracy and floor UX still require headset testing.

## Grouped watch controls

The watch has three 128 × 128 group buttons, replacing the two rows of small
function buttons. Every fan option has the same 128 × 128 target. The watch's
physical scale preserves the larger target size when space for the fan is added.

- **Calibration:** recenter, fix floor, remember/forget floor, full reset and
  mounting reset. Motoc mode shows Calibrate and Resume for its body controls.
- **Input:** keyboard, virtual D-pad and Hands as controllers. Enabled toggles
  stay highlighted when the group is reopened.
- **Utils:** fpsVR performance panel, configuration dashboard and layout editing.

Press and release on the same button to open a group. Only its options and parent
remain visible and actionable. Selecting an option or tapping the parent closes
it. A group transition clears both hands' armed presses; a held second pinch or a
release over a newly revealed button cannot trigger an action. Other overlay
clicks/touches and new grabs are blocked while the group is open. Hiding the
watch, losing XR focus or canceling interaction clears the modal state.

The existing reset countdowns and floor procedure are shared with custom panels.
The lower watch canvas is compacted to 760 pixels with 128-pixel action targets.
Compact battery blocks use 20-pixel icons and 16-pixel readouts, with wrapped rows
packed against the 628-pixel-wide watch body. The time is right-aligned in a
180-pixel clock face. Group buttons sit 8 pixels from it and 12 pixels apart.
Reset/walking and floor status share one line.
Battery/clock/status information and set/display controls return when the fan is
closed. No headset session is needed to test or preview the layout:

```sh
mkdir -p build/watch-preview
WIVRN_WATCH_LAYOUT_JSON="$PWD/build/watch-preview/layout.json" \
  cargo test --locked -p wayvr --no-default-features --features openxr,wayland,x11 \
  --manifest-path server/wayvr/upstream/Cargo.toml grouped_watch_native_layout_and_preview
QT_QPA_PLATFORM=offscreen uv run --no-project --with PySide6 python \
  fork/render-watch-preview.py build/watch-preview/layout.json \
  build/watch-preview/watch-menu-preview.png
```

The preview uses measured native WGUI button boxes, the production palette and
SVG assets. It is a layout preview, not a GPU/headset screenshot. The native test
checks hidden controls, target dimensions and positions in all four states;
separate state-machine tests check modality, matching releases, cancellation and
fan spacing. Hand comfort and tracking reliability still need headset validation.

## Hands as controllers

In the watch, open **Input → Hands as controllers**. This opt-in, session-scoped
switch presents native hand interaction devices as Touch controllers. While it is
enabled, applications report hand tracking as unsupported and receive no hand
joints. WayVR retains the complete 26-joint stream for its UI and the emulation
gestures. It applies only to WiVRn tracked hands; physical and externally owned
controllers retain their normal roles.

| Gesture | Controller input |
| --- | --- |
| Thumb + index pinch | Analogue trigger, trigger click/touch |
| Thumb + middle pinch | A (right), X (left) |
| Thumb + ring pinch | B (right), Y (left) |
| Close middle, ring and little fingers into a fist | Analogue grip/squeeze, grip click/touch |

Grip requires the three lower fingers to curl together. A single finger pinch
does not squeeze; a fist suppresses accidental face-button pinches. Pinch distances
are scaled by palm width; click hysteresis reduces jitter. Tracking gaps over
150 ms, invalid joints, disable, focus loss and socket disconnect release synthetic
inputs. A held gesture must return to neutral before it can activate after a mode
or ownership change. Raw skeleton collection remains available internally. Applications that already
created hand trackers receive inactive results until controller mode is disabled.
Applications that cache capabilities may need restarting after changing the mode.

Virtual D-pads can be enabled independently or together with this mode. They keep
the existing axes, touch and click transport. A hand owned by a D-pad suppresses
its controller pinches and grip, then requires a release before gameplay gestures
resume. The other hand remains available. Hovering WayVR overlays/keyboard or
opening a watch group also reserves gestures for the UI.

The existing same-user `hand-input.sock` protocol uses bit 5 to request controller
mode and bits 6/7 to reserve left/right gestures, retaining all existing pad flags.
The server requires fresh packets and a native hand profile for each hand. Older
servers reject the extended flags and leave controller mode unavailable.

Offline validation includes `fork/tests/hand_controller_test.cpp` for pinch,
analogue trigger, hysteresis, fist grip and release/rearming, and the extended
`fork/tests/hand_joystick_test.cpp` for independent/combined modes, per-hand
ownership, physical takeover, expiry and disconnect. Headset comfort and game
compatibility still need live validation; these checks do not install changes.

## Shora walking in place and body turning

The watch's **Input → Walking in place** switch feeds forward movement into the
left controller stick. **Input → Body turning** optionally feeds smooth turning
into right-stick X, but only while Walking in place is enabled **and physical leg
motion confirms an alternating gait**. Both switches are session-scoped and off
by default. Hand-controller pinches, squeeze and full skeleton delivery remain
independent. Motoc mode does not supply this Shora gait source.

First perform the normal full/mounting body calibration with real trackers
assigned to both thighs and both shins. Foot trackers are not required. Select
**Calibration → Walking setup**, stand upright and still for two seconds, then
march for eight seconds. Setup verifies alternating steps and emits no movement.
First use and recovery after tracking/calibration loss also require this setup.
The watch status and Shora tracking panel show the current instructions. The
setup command is also available from the Shora calibration selector.

The detector runs in Shora's existing 33 ms update loop, using calibrated sensor
rotations before output prediction/smoothing. Missing, duplicate, uncalibrated or
older-than-150-ms leg rotations invalidate the sample; heartbeats cannot extend
rotation freshness. Fresh HMD position is required to reject real horizontal
translation and tracking-origin jumps. Inferred FK feet cannot establish sensor
presence. Head orientation is not used to measure leg lift, so looking sideways
or down does not reverse or disable step detection.

Each leg has lift/return hysteresis; two alternating completed steps start the
cadence-based forward command. Simultaneous crouching, isolated/same-leg foot
adjustments and head bobbing do not start locomotion in the synthetic traces.
The forward command accelerates smoothly and decays when stepping stops. It is
always clamped and never requests a stick click or sprint button. The game's own
head/controller-relative movement setting determines forward direction.

Body turning uses fresh, calibrated hip/chest yaw relative to the relaxed neutral
heading. With both sensors, hip/chest weights are 70/30; either sensor can be used
alone. Missing torso sensors disable turning without stopping valid forward gait.
A tracker entering/leaving the set recaptures neutral instead of generating a
yaw jump. Turning has a deadzone, bounded gain and smoothing, with the proper
OpenXR-yaw-to-stick sign. Its gate additionally requires meaningful changes in
leg angle **and** leg shortening within a nominal 120 ms window; a lifted but
stationary knee does not sustain turning. Sampling and transport add latency.
Turning drops directly to zero when this gate closes, independently of forward
deceleration. While standing, neutral torso headings follow slow yaw drift.

Stick ownership is selected, never summed:

1. A deliberately moved physical stick (magnitude > 0.15) owns that stick.
2. A virtual D-pad owns its hand, including its neutral arming stage.
3. Otherwise, a valid enabled gait can supply left-forward/right-turn input.

WayVR UI interaction reserves both walking sticks. Ownership changes, stale
samples, disconnect, disabled mode or calibration generation changes release
synthetic input; resuming requires two new alternating steps. Right-hand D-pad
turning can coexist with left-hand walking. WiVRn controller-role selection also
supports walking with native hand tracking when the other hand emulation
switches are disabled. Externally owned controller devices are not replaced.

Shora publishes a bounded, nonblocking 48-byte `WVW1` datagram to the runtime-owned
`$XDG_RUNTIME_DIR/wivrn-pro/walking.sock`. The runtime verifies kernel-provided
same-user credentials, generation, sequence, timestamp, flags and finite bounded
axes; expired/replayed input cannot sustain movement. This is a separate channel
from the single-client hand-input endpoint. WayVR uses versioned `WVJ2` control
packets only while walking is enabled, keeping legacy hand/D-pad packets intact
when disabled. Input readers do not block the compositor/controller paths.

The Shora tracking settings UI exposes lift threshold, sensitivity, maximum
forward stick, stop response, turn deadzone, full-turn angle and maximum turn.
Apply validates and atomically saves them to the active startup TOML profile;
export also preserves the `[walking]` table. Changing these settings restarts
walking setup. Old profiles/clients may omit the table and receive defaults.

Validation covers synthetic gait/negative traces, noisy held knees, torso drift
and missing trackers, freshness independent of heartbeats, calibration changes,
settings persistence/export, the real datagram endpoint, replay/expiry, physical
sticks, D-pads and UI ownership. These are offline checks: false-positive rates,
comfort, latency and game-specific mappings still require headset/tracker trials.
No locomotion changes have been installed or enabled on a live session.
