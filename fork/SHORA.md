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
removed NVENC/x264/raw encoders become VAAPI, AV1/raw codecs become HEVC, and
Lighthouse is disabled, with changes reported in the journal. Supported settings
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
