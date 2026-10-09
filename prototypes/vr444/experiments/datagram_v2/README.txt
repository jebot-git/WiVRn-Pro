Bounded paired-frame datagram experiment (0.8)

Run from repository root:
 python3 prototypes/vr444/experiments/datagram_v2/test_transport.py
 python3 prototypes/vr444/experiments/datagram_v2/localhost.py --output /tmp/vr444-localhost-results.json
Only Python standard library required. All sockets bind/send to127.0.0.1 ephemeral
ports, one send then one receive with250us pacing. No root, ADB, external listener,
WiVRn integration, dependency installation or network configuration changes.

This is plain UDP, NOT authenticated QUIC or a production transport. CRC32 detects
accidental corruption and is forgeable. Session identifiers/epochs reject stale
traffic but do not authenticate a peer. The interface accepts trusted control state
through Receiver.configure; no control handshake/pairing is implemented.
Configuration requires monotonically increasing u32 epochs: changing UUID requires
strictly greater session epoch; same session epoch requires same UUID and greater
config epoch. No wrap/reuse is accepted during a Receiver lifetime. New-process
integration must establish fresh authenticated connection/control state so delayed
old packets cannot enter a new Receiver with repeated epochs.

Wire subset of the0.7blueprint, intentionally not a finalized interoperable profile:
48-byte little-endian VRP2 header (same field layout as draft). Manifest object0 is
single-packet, with16-byte session UUID, u64 receiver-clock deadline, u16 eye count,
u16 chunk count, u32 total compact size, then16-byte chunk descriptors and CRC32.
Each descriptor is chunk_id:u16, eye:u8, reserved:u8, length:u32, CRC32:u32,
eye_offset:u32. Both eyes must be nonempty, offsets contiguous per eye, chunk IDs
canonical. Chunk bytes are opaque independent source bytes, NOT a codec/dictionary.
Data object IDs select descriptors; index/count/length must match their bounds.
Unknown flags/types, FEC fields, reserved values, invalid counts and oversized
payloads reject. No compression, dimensions/colorspace, FEC, references, depth,
clock synchronization or reliable control included. Future integration must version
this narrower profile explicitly rather than assume full draft interoperability.

Plain UDP payload <=1200bytes:48application header +up to1152body. This differs from
the0.7QUIC example because no QUIC framing/AEAD is present. QUIC integration must
reduce usable body size to actual negotiated/path allowance and re-fragment.

Bounds:256KiB compact stereo pair;16KiB chunk;32chunks; two incomplete candidates;
64KiB total orphan payload;528384buffered candidate bytes;128retired IDs;32issued deadline tickets;4096trace
entries; fixed-name counters. Current published pair is separate and protected.
Stored-byte counters exclude Python objects/allocator and temporary assembly copies;
all relevant counts/sizes are capped, but this is not a heap-profiler measurement.
Maximum published payload adds256KiB; bounded temporary joins/copies also add memory.

Atomic publication requires manifest identity and every chunk/CRC for both eyes.
Incomplete frame supersession evicts only incomplete candidates, never published
pair. New complete frame replaces previous complete; older/equal frames then reject.
Conflicting warp or duplicate payload does not overwrite accepted data. CRC failure
retires whole pair. Configuration reset clears candidates, publication and retired
IDs; this is an explicit trusted control boundary, not automatic incoming config.

Deadlines belong to RECEIVER time.monotonic_ns. The receiver issues a ticket with
issue_deadline(now,ttl,frame,warp), 0<ttl<=100ms. At most32tickets are retained,
bound to frame/warp and current session/config state. Manifest deadline must exactly
match the issued value; sender-selected deadlines/extensions reject. Configuration
resets, expiry, retirement and completed-frame supersession remove relevant tickets.
Harness hands the value to sender in-process; no clock sync inferred. Sender time or Unix wall time must not be used. Packet receives call
expire; idle integration must also call expire on a timer. Orphans expire after20ms.
Expired complete publication is retained for possible reprojection, while its source
age remains known. After all assembly/CRC, the receiver re-reads its injected
monotonic clock immediately before publication and drops pairs at/after deadline.
Published timestamp is this fresh reading, not the packet-entry time. This is a
software deadline check, not a hard real-time scheduling guarantee. No optical claim.

Unit tests use explicit deterministic receiver-clock values. Socket tests use real
same-host monotonic clock and an actual6ms delay for expiry. Software send-to-receive
interval includes Python/syscall/scheduling and is NOT network-only latency or MTP.
Impairments are deterministic packet-list transformations before real sendto calls;
they do not emulate WiFi MAC, radio conditions, congestion, burst delay or bandwidth.
