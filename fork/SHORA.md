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

`wivrn-pro-shora.service` runs the existing `slimevr-server-rs` tracking backend
with these private paths and the fork's OpenXR manifest. `wivrn-pro.service`
Wants/After that unit, whose bounded startup check waits for its listeners; there is deliberately no Requires/BindsTo/PartOf relation.
Both preserve the shared runtime directory, so restarting Shora leaves the
WiVRn/Monado clients and bridge endpoints alive. The bridge is present for the
WiVRn runtime session, including its connection-recovery period; it is not a
system-wide tracker service when no WiVRn runtime exists.

The **bridge** is integral; the complete IMU fusion, skeleton solver, tracker UDP
server and Shora control UI remain in the independently installed backend. Its
existing configuration is retained. It may still need tracker calibration;
spatial room capture does not reset or recalibrate Shora automatically. Poses
reach WiVRn through its native SolarXR/feeder stage. No cross-app controller
calibration is involved in native Quest-to-PC room alignment.

Offline tests cover framed and fragmented traffic, replay limits, stale pose
handling, rejection of reset replay, duplicate endpoint ownership and a real
local Unix-socket backend restart. They use synthetic messages; no HMD or tracker
hardware is accessed.
