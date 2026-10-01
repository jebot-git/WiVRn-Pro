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

After connecting to the matching server and starting a streamed VR application,
open WiVRn's in-stream window (both thumbsticks), then **Settings → Room capture**.
**Start / recheck 12 markers** enables passthrough and hides streamed video while
keeping the stream connection and feedback running. The guidance panel follows
your head. Turn through four directions, hold steady for three markers in each,
and complete the reload verification pass. **Retry / recheck**, **Skip**, and
**Return to streaming** are panel buttons. Marker axes use WiVRn's Vulkan renderer
and share one projection layer with controller rays.

The workflow requires tracking, stable head/anchor poses, and successful local
anchor persistence. Reload verification rejects shifts above 3 cm or 3 degrees.
**12 is the configured budget, not a claimed Quest hardware limit or a new
hardware-validated maximum.** Partial capture remains explicitly partial. This
native UI has not yet been tested in a headset.

Returning to streaming keeps saved markers localizing at five snapshots per
second. WiVRn remains the foreground XR application: there is no second Quest
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
| `source_session`, `sequence`, `generation` | Client scene epoch, monotonic sequence, reference-space generation |
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
bridge service. The reader example is also installed as `wivrn-pro-spatial`.

## Offline verification

```sh
fork/test-offline.sh
python3 fork/check-android-protocol.py .cxx/Debug/BUILD_ID/arm64-v8a/compile_commands.json
```

The tests use mocked OpenXR dispatch and isolated temporary Unix sockets. They
cover anchor lifecycle, guided scanning/reload verification, rigid transforms,
multiple/late/slow readers, expiry, tracking loss, invalid numbers, replay and
generation handling, maximum geometry, socket ownership, wire round trips and
truncated packets. Actual PC/ARM compiler constants are compared without running
either compiled artifact. See [validation record](VALIDATION.md).

**Live HMD tests remain suspended until explicitly requested.** Offline checks do
not establish capture UX, spatial accuracy, headset performance or scene API
behavior on the device. See [source provenance](PROVENANCE.md).
