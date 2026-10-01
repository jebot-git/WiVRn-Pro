# Independent companion spatial server

Standalone Git repository. The server and client API can be used without the
VRM companion; the optional room importer recognizes its v1/v2 room files.

Working prototype tested on Quest Pro and **installed, unmodified WiVRn 26.9**.
The PC server is a separate process. Receiver applications keep their connections
when a tracking provider exits; saved geometry is separate from live localization.
Neither WiVRn binary, APK, runtime selection nor configuration is modified.

## What works on the tested headset

- The stock WiVRn provider uses `XR_MND_headless` to read headset/controller poses
  without submitting display layers. The actual test had a valid headset pose;
  inactive controllers were reported invalid, not replaced with fake poses.
- Eight concurrent PC receivers shared live tracking from one helper. Restarting
  only the headless provider kept Python receiver connections open and recovered
  tracking. WiVRn's D-Bus owner stayed unchanged.
- The separate **Companion Room Source** Quest app created, saved and reloaded
  three native spatial anchors. The user confirmed its cyan crosses stay fixed
  while moving their head. These markers belong to this new app, not WiVRn.
- The Quest app reported a **3.5045 × 3.5517 m stage rectangle** and exported **36
  scene entities**, including floor, ceiling, walls, desk and door/window frames.
  Native geometry and located poses reached two PC clients simultaneously.
- Switching back to stock WiVRn stopped the Quest source's XR session. Live Quest
  data was invalidated; cached geometry/marker identities stayed available.
  Returning to the source app restored the same saved markers.

**Continuous Quest anchor localization alongside foreground WiVRn is not working
in this prototype.** The installed native runtime does not advertise headless XR.
The independent server solves distribution and lifetime coupling on the PC; it
cannot override the Quest application's XR lifecycle. This limitation was tested,
not inferred from a device support list.

## Architecture and coordinate spaces

```text
Installed WiVRn ── OpenXR headless helper ──┐
                                         │
Separate Quest app ── ADB reverse tunnel ──┼── PC companion server ── Python client
                  (USB or wireless ADB)   │                     ├── other app
Saved room catalog / spatial cache ───────┘                     └── other app
```

`devices` are in **pc_stage**. `quest.devices`, `quest.anchors` and
`quest.scene.entities[].pose` remain in **quest_stage**. Stationary-controller
calibration establishes a measured yaw/translation transform between these
spaces; it does not assume STAGE, LOCAL FLOOR and application world origins match.

After successful calibration, `alignment.status` is `valid` and
`quest.alignment_to_pc` is `calibrated`. `quest_aligned` supplies live PC-space
poses when the Quest source is tracking, and `quest_archive_aligned` supplies
PC-space **cached** poses from the calibrated Quest session. The transform is
already applied to both views. Original coordinates and entity-local geometry
remain unchanged. Imported companion room layouts use a separate coordinate
system and are not aligned by this method.

Alignment is scoped to the measured broker/provider/Quest sessions and origin
generations. A new session, generation change, WiVRn owner change, or loss of PC
tracking invalidates it; recovery requires calibration again. Quest app pause or
transport expiry clears live poses while keeping matching cached coordinates
available. This is a cached-room handoff, not continuous native anchor localization.
The transform is not automatically restored after a bridge restart.

One receiver connection does not mean one additional VR compositor application.
This API serves desktop services and renderers alike; simultaneous VR rendering
still depends on the runtime's compositor support.

## PC build and launch

Server runtime dependencies: Python 3.11+ and `gdbus`. PC tracking additionally
needs the OpenXR loader and stock WiVRn in the same Linux desktop session. Building
the tracking helper requires a C++20 compiler, OpenXR development headers and
nlohmann-json headers.

The Quest app draws its diagnostic crosses directly with native OpenXR /
OpenGL ES; its separate build uses the Android SDK/NDK and JDK. The PC server,
Python receiver and live smoke test require no game engine.

```sh
./build.sh
python3 bridge.py --track
```

Without `--track`, the server still distributes saved spatial data and accepts the
Quest source. `--runtime /path/to/openxr_wivrn.json` selects the manifest for the
helper only. The default is `/usr/share/openxr/1/openxr_wivrn.json`.

The server reads only WiVRn connection/session properties. It resolves the unique
D-Bus owner before querying it so it does not activate a stopped WiVRn service.
A disconnected, restarted or replaced provider gets a new `provider_session`.
Live data expires after 750 ms; runtime errors cannot leave old poses valid.
For a headset transport pause of less than ten seconds, the bridge retains its
OpenXR helper to support Quest activity handoffs, but immediately clears poses
and alignment and suppresses helper samples until WiVRn reconnects. Helper exit,
WiVRn owner changes and longer disconnects still restart the provider. Calibration
measurements can therefore span a short handoff without changing provider identity.

The discovery files live under `$XDG_RUNTIME_DIR/vrm-room-bridge/` (mode 0700).
`endpoint.json` contains a receiver token; `quest-provider.json` contains a
separate publishing token; `calibration-provider.json` authorizes calibration
activation/clearing. All are mode 0600. Receiver connections remain read-only.
The server listens on an ephemeral
**127.0.0.1** TCP port, never on a public interface. `--directory` overrides this
runtime directory. Do not distribute the files or their tokens.

Example subscriber (run multiple copies):

```sh
python3 receiver.py
```

### systemd user service

Install from the checkout, as your desktop user:

```sh
./build.sh
python3 install_service.py
systemctl --user daemon-reload
systemctl --user enable --now spatial-bridge.service
```

This installs `spatial-bridge.service` under
`${XDG_CONFIG_HOME:-~/.config}/systemd/user/`, pointing at this checkout and its
built helper. Keep the checkout in place; rerun the installer if it moves.
`--catalog /path/to/catalog.json` preserves an existing custom catalog/cache
location, `--runtime /path/to/openxr_wivrn.json` selects another manifest, and
`--no-track` installs a saved-data/Quest-only service. `--unit-dir /tmp/units`
stages a unit for review. The installer writes the unit; the `systemctl` commands
reload and activate it. After reinstalling an active unit, restart the service.

Stop a manually launched bridge before starting the service: both use the same
discovery directory and its exclusive lock. On this development machine the
existing catalog is `.state/catalog.json`; install with
`python3 install_service.py --catalog "$PWD/.state/catalog.json"` to retain it.

When enabled, the bridge starts at user login and is also wanted by
`wivrn.service`. It can run before WiVRn, waits for a connected/running XR session,
and reconnects its helper after WiVRn restarts. Starting the bridge does not start
WiVRn. Stopping WiVRn leaves the bridge serving saved/Quest data and heartbeats.
WiVRn's unit/configuration is unchanged; the dependency is installed in the user
manager's `wivrn.service.wants/` directory. The bridge restarts on failure, and
stopping it terminates its helper. The service runs without a graphical interface.

```sh
systemctl --user status spatial-bridge.service
journalctl --user -u spatial-bridge.service -f
systemctl --user restart spatial-bridge.service
systemctl --user disable --now spatial-bridge.service
```

Clients rediscover the endpoint after a service restart because its port/token
change. Rerun `quest/launch.py` to refresh an active Quest tunnel and credential.
To remove the unit, disable it first, delete the installed unit file, then run
`systemctl --user daemon-reload`.

## Saved data independent of WiVRn

The default data folder is `$XDG_DATA_HOME/vrm-room-bridge/` (normally
`~/.local/share/vrm-room-bridge/`). `--catalog` overrides the catalog path and also
sets the directory for the native spatial cache.

Import a companion room explicitly:

```sh
python3 catalog.py \
  --import-room '/path/to/playspace.json' --room-id living_room
```

This atomically copies a companion v1/v2 room into `catalog.json`. The server
reloads edits while running. Invalid catalog updates retain the last good data.
The importer validates the envelope and size; consuming applications must still
validate geometry with their own room implementation before navigation.

`quest-spatial-cache.json` stores the native stage bounds, marker identities and
exported scene geometry with `saved_pose` fields. The server retains it while
WiVRn or the Quest app is stopped and reloads it after its own restart. Cached
poses are **not live poses**. All cached alignment starts unresolved; only caches
from the calibrated Quest source session/generation receive aligned views. Room
membership, semantic editing and conversion into the companion's navigation
polygons remain integration work.

The v1 limits are 32 KiB per catalog/cache, 24 KiB per Quest provider sample,
128 KiB per receiver line and 32 simultaneous connections. The Quest query loads
at most 64 scene entities; large payloads omit lower-priority entities and set
`scene.truncated`. A non-truncated response does not prove the entire building was
queried. Global triangle meshes and raw tracking point clouds are not exported.

## Separate Quest app

Package: `org.vrmcompanion.roomsource`. It installs beside stock WiVRn. On first
run accept the spatial-data permission. The app guides you through capturing
up to **12 app-owned markers: four directions, three markers per direction**.
An in-headset text panel appears over passthrough; a recording may show black
where passthrough is hidden by the OS. No game engine is required.

1. Turn in place through the four prompted directions. Each direction needs one
   second of steady, fully tracked head pose. This records directional coverage,
   not a guarantee of visual-map quality.
2. Follow the capture directions. Hold still for 1.5 seconds to create each
   marker; the three-marker group is fixed about 1.2 metres ahead with lateral
   and vertical separation. The app waits for successful saving and one second
   of stable localization before moving on.
3. Follow the directions again for verification. The app reloads each accepted
   UUID from storage and checks localization against its capture pose. A shift
   above 3 cm or 3 degrees requires retry or skip. This is a consistency check in
   the current tracking space, not a measured physical-accuracy guarantee.

Use **left-controller X to retry** a failed operation and **right-controller A
to skip** the current marker (or direction during the initial scan). A skipped
marker does not count as verified. At the end, X repeats the reload check. A
partial result is labelled as such; only 12 verified markers produces a complete
result. Loading/saving/localization has a 30-second workflow timeout. Head motion,
tracking loss or loss of input focus resets the stability hold and prevents new
capture. Missing passthrough also pauses capture.

Saved markers live under the app's private `guided-v1/` directory. Later runs
reload them; an interrupted capture resumes missing slots, recovering its layout
orientation from localized saved anchors. The old `test-marker-*.uuid` files are
retained separately. Retry never silently replaces a saved anchor. Restart the
app to revisit slots skipped during capture; do not clear app data to retry.

The renderer queries `maxLayerCount`, reserves layers for passthrough and the
prompt, and prioritizes the current marker if it must limit visible crosses.
The usual full display uses 14 layers: 12 markers, passthrough and the prompt.
Markers share the provider's 24 KiB budget with scene data; lower-priority scene
entities may be omitted. The cap of 12 is an engineering target, not a
hardware-validated reliability maximum. A wearer completed the full guided flow
on Quest Pro: all 12 markers were saved, reloaded and verified, with all four
directions covered and no skips. This is a successful device run, not a guarantee
across rooms or tracking resets.

Subscribers receive progress in `quest.capture` (`phase`, `stage`, `slot`,
`accepted_count`, `verified_count`, `coverage`, `skipped`, `complete`, and prompt
text). Each marker includes `slot`, `verified` and `skipped`. Verification is a
historical check: always also check live `valid`, Quest status and TTL. Capture
does not align Quest coordinates to PC coordinates.

```sh
quest/build.sh
python3 quest/launch.py \
  --endpoint "$XDG_RUNTIME_DIR/vrm-room-bridge/quest-provider.json" --install
```

Build requirements: Android SDK/platform 36/build-tools 36.1, NDK 29, Java 21,
OpenXR Android arm64 loader plus matching headers. Set `ANDROID_HOME`,
`ANDROID_NDK_HOME`, `JAVA_HOME`, `OPENXR_ANDROID_LOADER` and `OPENXR_HEADERS` as
needed. Provide a standard arm64 Android OpenXR loader and its headers using these
variables, or put them in `quest/.deps/libopenxr_loader.so` and
`quest/.deps/include/`. These are standard SDK dependencies, not WiVRn binaries.
The local development copy has these SDK artifacts cached outside Git.
The Quest app compiles the included `quest/native/anchor_manager.cpp`; no WiVRn
source checkout, avatar project or WiVRn networking code is required.
Keep the debug signing key and application data if retaining test anchors.

The launch tool passes the provider token without printing it and forwards
headset `127.0.0.1:47845` to the server's current local port. After restarting the
PC server, run the launcher again to refresh the tunnel and credential.

After completing the prompts, verify delivery to two PC receivers with:

```sh
python3 quest_smoke.py --endpoint "$XDG_RUNTIME_DIR/vrm-room-bridge/endpoint.json"
```

The test allows five minutes for interactive capture. Use `--expected-markers N`
for a deliberately partial capture and `--timeout SECONDS` to adjust the wait.

### Physical controller calibration

Both sources expose controller **grip** poses, tracking flags and interaction
profiles. Touch Pro controllers can serve as stationary physical references while
the headset switches between the capture app and WiVRn. This requires neither
simultaneous foreground XR sessions nor comparing timestamps across devices.

Install the current APK, leave WiVRn connected, and place both controllers on
stable surfaces at least 50 cm apart. Keep them awake and untouched, and keep the
headset awake and tracking. Use the activity of the installed WiVRn package:

```sh
python3 controller_calibration.py --serial HEADSET_IP:5555 \
  --wivrn-activity org.meumeu.wivrn/.MainActivity \
  --output evidence/controller-calibration.json
```

The tool takes four-second stationary windows in WiVRn, switches to the Quest
app's controller-calibration mode, measures again, and returns to WiVRn for a
third window. It then submits the measurements to the running bridge, which
independently recomputes the fit and activates it. Calibration mode pauses marker
creation and saving, and reloads existing guided markers so their poses belong to
the new calibration session. Scene-only captures are also cached. Both sides
must report active position and orientation tracking with the same interaction
profiles. Within-window motion above 1 cm / 1 degree, PC round-trip differences
above 2 cm / 2 degrees, and session/generation changes reject the measurement.

The solver fits yaw and XYZ translation from the left grip pose, keeping scale
at one. The right controller is held out and must agree within 3 cm / 3 degrees.
The report contains `quest_stage` → `pc_stage` translation, an XYZW quaternion,
source session identities, raw samples, stability and validation residuals.
Thresholds are acceptance criteria, not guaranteed device accuracy. A failed
attempt also writes a report and attempts to restore WiVRn.

The first Quest Pro hardware run passed with controllers 1.023 m apart: the
held-out right controller differed by 0.14 mm / 0.007 degrees. This measures
cross-app consistency at two positions, not absolute or room-wide accuracy.
The fitted transform also closely matched the inverse runtime stage offset.

Use `--report-only` to measure without activating, or `--clear` to invalidate the
current alignment. `--apply-report FILE` submits existing measurements only if
their session/generation identities still match and the PC return measurement is
no more than 30 seconds old. The bridge recomputes from the observations rather
than trusting a report's transform or success flag. Reports remain audit evidence;
they are not restored as active calibration after restart.

Subscribers receive these additional fields:

| Field | Meaning |
| --- | --- |
| `alignment` | Status, calibration ID, `quest_stage` → `pc_stage` transform, source identities and measured errors. Invalidated results expose a reason. |
| `quest_aligned` | PC-space `devices`, `anchors`, scene entity poses and the transformed stage rectangle; available only with live Quest tracking. |
| `quest_archive_aligned` | PC-space marker and scene `saved_pose` fields, `status: cached`, `live: false`, capture time and calibration ID. |

The aligned views contain poses, not duplicate mesh/polygon data. Join scene
entries by UUID to their original geometry; vertices and boxes remain local to
the entity and use the aligned entity pose. The stage rectangle likewise needs
its aligned pose as well as width/depth. Invalid source poses are omitted.
Unavailable views have `status: unavailable` and a reason, with no usable poses.
If a large snapshot exceeds the 128 KiB budget, derived views are omitted first
with `reason: snapshot_size_limit`; the transform and original data remain.

The local control protocol uses a separate calibration credential. Send a v1
`calibrate_controllers` hello, then three newline-delimited objects containing
`phase` (`pc_before`, `quest`, `pc_after`, in that order) and its `samples` array.
Each phase is bounded by the line limit and 128 samples. The response is a
`calibration_result` with `accepted` and either `calibration_id` or `error`.
A `clear_calibration` hello with the same credential clears the active result.
Ordinary receiver and Quest-provider credentials cannot perform these operations.

### Background service or 2D panel after capture

An Android foreground service could keep a transport/cache process alive after
the immersive activity pauses, subject to Android lifecycle restrictions. It does
not grant an OpenXR session permission to keep tracking.
[Android foreground services](https://developer.android.com/develop/background-work/services/fgs)

A separate Android panel activity is viable for capture status and a return-to-
capture button. Meta supports 2D windows over an immersive app, and its Spatial
SDK provides a hybrid 2D/3D sample. A panel is not evidence that a second immersive
anchor session can continue alongside WiVRn.
[Meta multitasking requirements](https://developers.meta.com/vr/resources/vrc-quest-input-4/),
[Spatial SDK hybrid sample overview](https://developers.meta.com/horizon/documentation/spatial-sdk/spatial-sdk-development/)

On this Quest Pro, the earlier hardware test showed the capture XR session
stopping when WiVRn takes over, and the runtime did not advertise headless XR.
Neither a background-service nor a 2D-panel version has been implemented or
device-tested. The current supported handoff is to finish capture, keep saved
geometry/UUIDs in the PC cache, and relocalize when returning to the Quest app.
Continuous live Quest localization during WiVRn remains unverified and would
need additional runtime support or integration into the foreground XR application.

### Wireless ADB

**Hardware verified with the USB cable physically unplugged:** two receivers
shared 474 snapshots over a 15-second observation, with three localized anchors
and all 36 exported scene entities. `adb devices -l` showed no USB transport and
the reverse tunnel belonged to the network connection. A non-VR PC receiver
also consumed ten live Quest samples while WiVRn reported its headset disconnected,
confirming that the spatial server does not require an active WiVRn connection. See
`evidence/quest_room_source_wireless.json`.

USB is not a protocol requirement: select an already connected wireless ADB
transport explicitly. With network debugging enabled and authorized on the
headset:

```sh
adb connect HEADSET_IP:5555
python3 quest/launch.py \
  --serial HEADSET_IP:5555 \
  --endpoint "$XDG_RUNTIME_DIR/vrm-room-bridge/quest-provider.json"
adb -s HEADSET_IP:5555 reverse --list
```

`--install` also works over the selected wireless transport. Enabling legacy
network ADB initially can require USB (`adb -s USB_SERIAL tcpip 5555`) or a
headset/system tool; that is a separate device debugging setting. This prototype
still requires an **active ADB connection**, wired or wireless, for its tunnel.
A normal authenticated LAN transport with pairing and encryption would remove
that developer-tool dependency; it is not implemented here. After a device or
ADB restart, reconnect wireless ADB and recreate the tunnel. To return an attached
headset to USB-only ADB, use `adb -s HEADSET_IP:5555 usb`.

## Meta boundary, scene and anchor APIs

These are different data sources, with different guarantees:

| Data | What Meta exposes | Current prototype |
|---|---|---|
| Stage play area | OpenXR `xrGetReferenceSpaceBoundsRect`: a rectangle in STAGE | Read successfully on Quest Pro; unavailable through stock WiVRn |
| Drawn Guardian outline | Legacy `OVRBoundary.GetGeometry(OuterBoundary)` is documented, but the OuterBoundary enum is deprecated for OpenXR | Not claimed available; no tested native polygon API here |
| Room geometry | `XR_FB_scene`, scene entities with semantic labels, 2D boundaries and 3D boxes; requires spatial permission | 36 entities exported from Quest Pro |
| Persistent anchors | FB create/save/query/locate operations, app-owned UUIDs | Three saved and reloaded markers |
| Shared anchors / rooms | Meta shared-anchor/group and Space Sharing APIs on supporting firmware | Not implemented; do not infer support merely from API documentation |

The floor entity's boundary is room geometry, not the user's Guardian outline.
`XR_META_boundary_visibility` concerns visibility, not exporting a boundary.
Meta's documentation explicitly includes Quest Pro for Scene and spatial-data
permission. A missing depth sensor does not prevent access to an existing room
model.

Geometry and located poses can be serialized and sent to another computer; this
prototype demonstrates it. MRUK also documents scene JSON export/import. That
exports application-visible geometry, not a portable native localization engine.
An anchor UUID sent to an arbitrary PC does not let that PC relocalize the anchor.
Native cross-device relocalization uses Meta's supported sharing/loading flow;
Meta Link support is not evidence of support through WiVRn on Linux.

Sources checked 2026-10-01:

- [Meta Scene overview, including Quest Pro](https://developers.meta.com/vr/documentation/native/android/openxr-scene-overview/)
- [Meta Scene query/geometry API reference](https://developers.meta.com/horizon/documentation/native/android/mobile-scene-api-ref/)
- [Meta spatial-data permission](https://developers.meta.com/horizon/documentation/unity/unity-spatial-data-perm/)
- [Meta boundary API and deprecations](https://developers.meta.com/vr/documentation/unity/unity-ovrboundary/)
- [OpenXR stage bounds contract](https://registry.khronos.org/OpenXR/specs/1.0/man/html/xrGetReferenceSpaceBoundsRect.html)
- [MRUK scene JSON serialization](https://developers.meta.com/horizon/reference/mruk/v205/class_meta_x_r_m_r_utility_kit_m_r_u_k/)
- [Meta native Space Sharing](https://developers.meta.com/vr/documentation/native/android/openxr-space-sharing-api-ref/)
- [Meta Shared Spatial Anchors](https://developers.meta.com/horizon/documentation/unity/unity-shared-spatial-anchors/)

## Protocol and verification

JSON Lines v1 over TCP. A receiver sends one hello:

```json
{"version":1,"op":"subscribe","token":"FROM_ENDPOINT_FILE"}
```

It receives complete snapshots, including an immediate snapshot for late joiners
and heartbeats while WiVRn is stopped. Slow receivers have bounded queues and are
disconnected without blocking other clients. Receiver connections cannot publish
or delete data. The separately authenticated `provide_quest` role permits one
Quest source at a time. A source pause/disconnect/timeout clears its live poses.
The broker admits 32 simultaneous connections, counting subscribers, the Quest
provider and pending authentication handshakes together (31 subscribers when a
Quest provider is connected). All subscribers share one PC tracking helper;
joining/leaving does not create another OpenXR session. Excess connections are
closed and slots become available again on disconnect.

Track `session`, `provider_session`, `generation`, `quest.source_session` and
`quest.generation`; reset derived transforms when these change. Expire data using
`sample_age_ms` / `quest_age_ms` plus elapsed receiver time and `ttl_ms`. Closing
the socket or missing heartbeats invalidates live data. A provider error or
missing capability must not be interpreted as empty-but-valid room geometry.

```sh
python3 -m unittest -v test_bridge
# Opt-in hardware test: WiVRn must already be connected; leaves it running.
python3 live_smoke.py
# With the Quest source already running; cycle returns to the source preview.
python3 quest_smoke.py \
  --endpoint "$XDG_RUNTIME_DIR/vrm-room-bridge/endpoint.json" --cycle-wivrn
```

See [validation results](docs/VALIDATION.md). Hardware tests generate local
`evidence/room_bridge_live_smoke.json` and `evidence/quest_room_source_hardware.json`
reports; captures are deliberately excluded from Git. Automated WiVRn owner-change
recovery is tested with a simulated D-Bus owner change; the hardware test restarts
our helper, not the user's WiVRn service. The prototype does not yet handle
arbitrary future WiVRn/OpenXR API changes without revalidation.

Stop a foreground server with Ctrl-C, or the service with
`systemctl --user stop spatial-bridge.service`. Stop the separate Quest preview and remove its
forward using the selected ADB transport:

```sh
adb shell am force-stop org.vrmcompanion.roomsource
adb reverse --remove tcp:47845
```

The PC server/client source is MIT-licensed. The Quest source and its reused
anchor manager are GPL-3.0-or-later; distribute corresponding source when shipping
that APK. OpenXR loader (Apache-2.0), nlohmann-json (MIT), Android/NDK components
and their notices remain separate dependencies. No avatar files are included.

## Reuse in another project

Start `bridge.py` once per desktop user, or enable its systemd service. Other
projects can use the `receiver.subscribe()` async generator from Python, or
implement the JSON Lines subscription protocol described above. Each project
owns its rendering, calibration, navigation and handling of stale live data.

The helper protocol and external JSON Lines protocol are versioned independently
of WiVRn's private network protocol. The optional catalog importer supports the
companion's saved-room format; native Quest spatial data and tracking do not
require that importer or an avatar application.
