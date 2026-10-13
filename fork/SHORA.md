# Embedded Shora bridge

`server/shora` is a Rust static library linked into the WiVRn server. C++ owns it
as part of `wivrn_session`; the Rust runtime/thread starts only in the server's
runtime child after WiVRn forks. Shutdown stops and joins the bridge before the
runtime process exits. There is no `shora-proxy` sidecar or shell launcher.

The integral Monado SolarXR driver connects only to this fork's private sockets:

| Socket under `$XDG_RUNTIME_DIR/wivrn-pro/` | Role |
| --- | --- |
| `SlimeVRRpc` | Stable public SolarXR bridge endpoint |
| `SlimeVRInput` | Stable public HMD/controller feeder bridge endpoint |
| `ShoraRpc.backend` | Restartable tracking backend SolarXR endpoint |
| `ShoraInput.backend` | Restartable tracking backend feeder endpoint |
| `ShoraControl` | Backend's control endpoint |
| `bridge.sock` | Integral spatial API, separate SEQPACKET protocol |

The directory is private (0700), public bridge sockets are 0600, and peers must
have the same UID. An occupied endpoint fails startup instead of replacing a live
publisher. Sockets are removed only if their inode still belongs to this bridge.
The Monado patch removes fallback to global/Flatpak SlimeVR sockets, avoiding
accidental attachment to the stock proxy.

The relay allows 16 clients, caps frames at 1 MiB and replay state at 2 MiB per
connection, retries every 250 ms and bounds writes at two seconds. Only SolarXR
setup/subscription state and feeder identities are replayed. Feeder poses are
coalesced and replayed only while under one second old. Reset/calibration RPCs
are never queued for replay across an outage. Slow/invalid clients are disconnected.

## Native tracking worker

The current local `slimevr-server-rs` source is vendored in `server/shora/backend`.
It includes tracker UDP and HaritoraX serial input, skeleton solving, reset and
mounting calibration, smoothing/prediction, drift correction, Stay Aligned,
SolarXR, control API, and face/eye OSC with the OpenXR feature enabled.
The frontend's Qt6 Widgets, skeleton preview and calibration panel are reused
in `dashboard/shora`; WiVRn streaming, pairing and systemd controls share that UI.

The backend is a Rust library linked into **the same `wivrn-pro-server` binary**.
WiVRn's parent process supervises an internal `--shora-worker` personality,
executes it before serving sessions, restarts it after failure, and terminates it
on shutdown. A parent-death signal also prevents an orphan after a parent crash.
Rust threads are never created in the parent that subsequently forks Monado.
Worker failure does not stop an active WiVRn runtime. There is one user service,
`wivrn-pro.service`, and no dependency on a separately installed Shora executable.
Tracking acquisition persists between headset sessions; expired/disconnected HMD
poses are cleared rather than anchoring a new session to the previous pose.

The worker receives private socket paths and the fork's matching OpenXR manifest.
Face/eye input uses the existing headless OpenXR implementation; it is not a second
Android app and does not require another Quest-to-PC alignment. It still needs
WiVRn's runtime and the headset's tracking permissions when enabled. Room capture
does not silently reset Shora's tracker calibration.

## Configuration carryover

At first server startup, missing fork files are imported from the user's config
home (`XDG_CONFIG_HOME`, or `~/.config`):

| Source | Fork destination |
| --- | --- |
| `wivrn/config.json` | `wivrn-pro/config.json` |
| `wivrn/known_keys.json` | `wivrn-pro/known_keys.json` |
| `wivrn/cookie` | `wivrn-pro/cookie` |
| `shora-rust/server.toml` | `wivrn-pro/shora.toml` |

Existing destinations always win. Imports publish complete 0600 files without
replacing originals. WiVRn preserves unknown keys and custom encoder layouts;
removed x264/raw encoders become VAAPI (NVENC is retained), AV1/raw codecs become HEVC, and
Lighthouse stays disabled until Motoc bridge mode is explicitly selected, with migration changes reported in the journal. Supported settings
are preserved. Malformed legacy JSON is reported instead of silently discarded.
The Shora TOML is copied byte-for-byte. Worker CLI arguments override only its
three IPC paths and UI mode. A fresh profile disables serial and face input until
configured. Profiles supplied via WiVRn's `-f` remain authoritative for streaming.
Arbitrary standalone Shora CLI options or systemd environment overrides are not
inferred; put the desired values in `wivrn-pro/shora.toml`. Referenced files and
devices must still exist; relative paths retain process-working-directory semantics.

The dashboard preserves advanced WiVRn JSON while editing ordinary fields and
checks for external changes before saving. “Save active tuning into profile”
merges tuning into the entire tracking profile, retaining face/OSC, serial and
assignments; this explicit action rewrites TOML formatting/comments. Startup
profile changes apply on restart. The source backend's runtime calibration offsets
are still not persisted; this integration does not claim feature parity with the
Java SlimeVR server or add raw-IMU fusion, full AutoBone or controller constraints.

## Validation

Unit tests cover the backend, reconnect bridge and skeletal model. Simulated
worker tests use temporary sockets, ephemeral UDP ports and fake serial PTYs.
They exercise acquisition, calibration, HMD anchoring/expiry, disconnect/reconnect,
Qt control and saved-profile reload. A separate isolated D-Bus mock tests streaming
settings and pairing. The production C++ supervisor is tested with a harmless
worker to verify automatic restart and shutdown. No live headset tests run.

## Spatial reference protection

The managed worker subscribes read-only to `wivrn-pro/bridge.sock` using the
supervisor-supplied `WIVRN_SPATIAL_SOCKET`. The subscription checks the peer UID,
record size, source session/generation, increasing sequence, monotonic sample
time, tracked head pose and 750 ms freshness. Heartbeats cannot keep an old
sample alive. Reconnection, expiry and reference changes discard drift-learning
history and pause Stay Aligned, preserving the correction already applied.
A fresh HMD feeder sample is also required; losing it pauses correction.

After two seconds of continuous healthy spatial samples, **a deliberate full or
yaw reset is normally required** to resume automatic correction. A verified
same-session rigid frame change can now recover automatically, as described below.
The first deliberate reset after an outage starts a new drift baseline; it does
not learn the reference jump as IMU drift. Mounting resets do not unlock correction.
The shared reset path enforces this for Qt,
tracker buttons and SolarXR requests, and ignores stale tracker rotations.
Rejected SolarXR requests are logged and receive no FINISHED response.

With three or more tracked persisted anchors, the guard compares their identities
and poses in Quest STAGE against the baseline. Fewer than three common anchors,
more than 5 cm translation or 3 degrees rotation triggers settling and frame-change
verification (or an explicit reset if verification fails). Once anchor validation starts, loss of anchors cannot silently
fall back to tracking-only mode during that worker's lifetime. Before three
anchors are available, status explicitly reports `tracking_only_no_anchor_validation`;
session/generation and freshness protection still apply. These thresholds are
initial guard thresholds, not a measured accuracy specification. Anchor drift
below them and common errors shared by the headset and anchors remain unobservable.

Qt shows reference health, anchor count and the reset requirement, blocks resets
while tracking is unhealthy, and cancels an outstanding calibration countdown on
reference loss. Shora continues using its existing HMD feeder frame. This implementation does not directly measure or
correct individual limb yaw from room anchors. Use the Quest calibration guide
in [SPATIAL.md](SPATIAL.md) to make deliberate resets more repeatable.

Existing TOML settings are unchanged, including the mutual exclusion between
Stay Aligned and drift learning. The guard is volatile; standalone workers without
`WIVRN_SPATIAL_SOCKET` retain their existing behavior. A managed worker with an
unavailable spatial bridge remains paused rather than learning against an unknown
reference.

### Automatic reference recovery

The native client locates both the HMD feeder input and the spatial anchors in
Quest STAGE. The bridge's PC-STAGE alignment is applied downstream by the runtime;
it is deliberately not fed back into tracker calibration. A PC-only stage offset
must not rotate the body twice. Head position already follows the feeder, so Shora
does not add an extra positional correction.

For a same-session generation change, or an anchor change crossing the guard's
5 cm / 3 degree thresholds, the guard retains the old anchor baseline temporarily.
It estimates `new_from_old` from matching anchor orientations and positions:

- At least three common persisted, tracked anchors must form a spatial triangle
  with cross-product magnitude at least 0.05 square metres (twice its area).
- Every common anchor must agree with one yaw-only rotation within 1 degree and
  with the corresponding translation within 2 cm. No outlier is silently removed.
- The fitted transform must remain within 1 degree / 2 cm of its first estimate
  for two seconds of fresh samples. Pitch/roll changes above the angular tolerance,
  changing identities without enough overlap, and unstable estimates fail closed.
- Session changes, socket disconnects, stale/replayed/malformed samples and ordinary
  tracking loss cancel recovery. A generation transition may contain non-live
  snapshots before its first tracked sample, but only within the existing 750 ms
  freshness bound. Loss of the fresh HMD feeder cancels automatic unlock.

Only calibration that was already unlocked before the transition can recover.
On success, Shora left-multiplies the tracker's current and easing-target yaw fixes
by the accepted yaw rotation, and rebases Stay Aligned's stored orientations.
It preserves mounting, body pitch/roll, accumulated automatic yaw offsets and
calibration revisions. It does not call a full/yaw/mounting reset. The change is
consumed once by reference revision, and logged as `spatial reference automatically
rebased; no drift observation`.

Drift history/rate remains discarded and its already-applied correction remains
held. Future deliberate resets establish a fresh drift baseline and then new
observations. Stay Aligned resumes with its offsets preserved and fresh rest
observations. Any failed verification keeps the existing manual-reset requirement;
simply waiting for anchors to settle does not bypass it.

These tolerances are conservative engineering defaults, not measured headset
accuracy. Correlated SLAM errors shared by all anchors can still look like a rigid
frame change. This is reference continuity recovery, not absolute body tracking,
individual IMU drift measurement, or unattended calibration after a restart.

Additional pose support is explored in
[Stay Aligned pose expansion](../server/shora/backend/docs/pose-expansion.md).

Offline integration test (fake spatial publisher, serial PTY and HMD feeder):

```sh
python3 fork/tests/spatial_tracking_smoke.py --server build/server/server/wivrn-pro-server
```

Automatic recovery validation (2026-10-06): the Rust workspace passed 130 tests
(including doctests); the fork Python suite passed 19 tests; the Debug Linux
server rebuilt successfully; and the simulated spatial worker test passed.
Recovery tests cover signed yaw (including near 180 degrees), translation,
repeated transitions, arbitrary bent-limb preservation, no drift learning,
outliers, degenerate layouts, pitch/roll, stale/replayed samples, HMD loss,
disconnects, initial calibration gating, and downstream PC alignment changes.
Hardware accuracy and worn-tracker behavior remain unverified.

### Anchor-validated rest, more poses, and HaritoraX controls

The unified Tracking page now includes opt-in anchor-validated rest locks and
calibration entries for high kneeling, sitting on heels, prone, and prone with
bent knees. The dongle sensor panel exposes readback-verified sensor modes,
individual correction flags and 50/100 FPS. See
[calibration steps and limitations](../server/shora/backend/docs/pose-expansion.md).
The hardware controls keep dongle communication and do not switch to Bluetooth.

Software validation for this extension: 139 Rust workspace tests including doc
tests, 20 Python unit tests, native server build, fork policy check, integrated
tracking/Qt and spatial-reference smoke tests, and
`fork/tests/haritorax_settings_smoke.py` with a simulated serial dongle. The latter
checks preserved settings bits, physical-versus-extension targeting, invalid FPS,
fresh echo confirmation, unconfirmed writes, no retries, and continued command
processing while waiting. Hardware validation remains outstanding.
