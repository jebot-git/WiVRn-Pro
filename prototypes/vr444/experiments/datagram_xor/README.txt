VXF1 bounded stereo XOR datagram experiment (2026-10-09)

Status
Offline reference only. No socket, WiVRn transport, headset, ADB, encryption,
authentication, congestion controller, clock synchronization or control handshake
is implemented. CRC and XOR parity do not authenticate a sender. VXF1 is a new
experimental packet version and is NOT compatible with VRP2, RTP FEC or QUIC wire
formats. It retains the existing datagram_v2 receiver's ticket/epoch/expiry rules
through an explicit shared base class; all VXF1 packet parsing is separate.

Motivation and decision
Abandonable independent stereo pairs can avoid waiting for old lost media before
accepting a newer complete pair. One XOR parity shard per stripe can recover one
missing source shard without a retransmission round trip. It cannot recover two
missing source shards in the same stripe, and parity/manifest loss can defeat
recovery. More parity consumes bandwidth, can increase queues, and may harm clean
links. Nothing here demonstrates improved headset latency. The live gate starts
at earliest frame receive, so upstream transfer alone is not its whole interval;
remaining stereo assembly and obsolete-frame blocking can still matter.

Reproduce from repository root
  python3 prototypes/vr444/experiments/datagram_xor/test_transport.py
  python3 prototypes/vr444/experiments/datagram_xor/matrix.py /tmp/vxf1-matrix.json

Wire, little-endian
44-byte header <4sBBHIIQQHHHHI>:
magic='VXF1', version=1, kind u8 (manifest1/data2/parity3), flags u16=0,
session epoch u32, config epoch u32, frame u64, warp u64, reserved u16=0,
index u16, count u16, body length u16, IEEE CRC32 u32. CRC covers header bytes
0..39 followed by the body, so identity, bounds and payload are checked together.
Epoch/frame/warp are nonzero. Maximum UDP application payload is 1,196 bytes:
44-byte header plus a 1,152-byte shard. The absolute input cap remains 1,200 bytes.

Manifest body <16sQIIIIBBH> is exactly44 bytes:
session UUID16, receiver-issued deadline u64, left length u32, right length u32,
left CRC32 u32, right CRC32 u32, stripe width u8, reserved u8=0, source count u16.
Its header index/count is0/1. The sender repeats the exact manifest after the first
source shard, before a lost first manifest could overflow the orphan budget. Both
copies count toward every byte/packet total. Losing both copies prevents assembly.

Source bytes are left||right, split into1,152-byte pieces. Data indices are global
0..N-1, count=N. A shard may cross the eye boundary; neither eye publishes alone.
The final shard is the exact remaining length. Stripe width is0 (no parity),4,8
or16. Source stripe g contains [g*K,min((g+1)*K,N)). Its parity is the bytewise XOR
of those shards with zero-filled tails, always1,152 bytes. Parity index=g and
count=ceil(N/K). This deterministic geometry needs no unbounded recovery metadata.
The last stripe can be short. Each stripe's parity follows its source shards.

When exactly one source shard is missing and parity exists, the receiver XORs the
known shards with parity. Recovered final-shard padding must remain zero. Every
source byte must be present and both final eye CRCs must match before publication.
The actual injected receiver clock is re-read after recovery/CRC and immediately
before publication; at/after deadline rejects the whole pair. Previous complete
publication remains available for reprojection when an incomplete pair expires.

Bounds and lifecycle
Both eyes nonempty; combined source<=256KiB, N<=228, parity groups<=57. At most two
incomplete candidates,64KiB total orphan payload,32 receiver-issued deadline
tickets,128 retired IDs and4,096 trace events. Every relevant count is bounded.
Buffered candidate payload cap is 2*(262144 +57*1152 +44)=655,704 bytes. This
counts stored payload bytes, not Python object/allocator cost. Published stereo
adds at most256KiB; bounded join/eye copies and a1,152-byte recovery scratch add
temporary memory. There is no unbounded queue or frame history.

An incoming frame/warp must have a current receiver-issued ticket even before its
manifest. Tickets use receiver monotonic time with lifetime<=100ms, cannot be
extended/reused, expire on idle timer/receive, and are removed on retirement,
publication/supersession and config reset. Idle integration must call expire().
Changing UUID requires a strictly newer session epoch; same-session config must
advance. New-process integration must establish fresh authenticated state.
Only incomplete older candidates are evicted. Conflicting duplicates/warps do not
overwrite accepted bytes. Old/equal completed frame IDs cannot replace publication.
On final-eye CRC failure or recovered-padding failure, the entire pair is retired.

Evidence
24 targeted tests pass, including every single packet loss for each parity width,
multiple stripes, two missing sources in one stripe, lost parity, exact stereo,
packet corruption, CRC-valid forged parity followed by final-eye rejection,
deadline-at-publication, stale/config identity, duplicates, candidate/orphan caps,
maximum256KiB pairs, every sample truncation and10,000 bounded malformed inputs
(half with recomputed packet CRCs to reach semantic parsing).

matrix.py runs1,008 deterministic cases: six sizes including3-byte/partial/exact
shard boundaries and256KiB, widths0/4/8/16, three seeds and14 loss/reorder/corruption/
expiry scenarios. Every publication is checked byte-exact, complete and before
deadline; state bounds are checked after every record. Synthetic arrivals advance
10us per transformed record; selected delays are60ms against a50ms ticket. These
are test clock inputs, NOT measurements of processing, UDP, WiFi or a headset.
Publication counts across intentionally failing cases are not reliability rates.

Counted UDP application bytes, including duplicate manifests and all parity:
pair source bytes   no parity      width4         width8         width16
32,768               34,220         43,788         39,004         36,612
65,536               68,220         86,160         77,788         73,004
262,144             272,352        340,524        307,036        290,292

For256KiB, parity adds25.03%,12.73%,6.59% relative to this profile's no-parity wire.
For a3-byte pair the fixed full parity shard adds536%; a negotiated minimum-size
or stripe policy is needed if such packets matter. Accounting also reports IPv4
and IPv6 totals using28/48 extra bytes per datagram, excluding link/WiFi/MAC,
security, ACK/control and congestion overhead. Do not use these as observed rates.

Primary research and alternatives (consulted2026-10-09)
https://www.rfc-editor.org/rfc/rfc9221.html
QUIC DATAGRAM supplies protected unreliable delivery within a congestion-controlled
QUIC connection. It still waits for congestion/pacing availability and does not
provide DATAGRAM fragmentation. An implementation must negotiate support and use
the actual path/peer payload allowance after QUIC/AEAD overhead. VXF1's1,196-byte
plain-UDP packet cannot simply be nested inside a1,200-byte QUIC packet. App-level
deadline/identity/bounds remain necessary. QUIC correctness, CPU cost and scheduling
have not been measured here.

https://www.rfc-editor.org/rfc/rfc5109.html
XOR parity for media is an established FEC building block with explicit protected
groups. This prototype deliberately uses simpler fixed stripes and separate
framing, not RTP ULPFEC compatibility. It exposes the one-missing-source limit and
counts all extra bytes. Production FEC must remain inside congestion control;
adding redundancy is not a substitute for a sustainable offered rate.

https://www.rfc-editor.org/rfc/rfc9000.html#section-2
Independent reliable QUIC streams can isolate stream ordering, but each missing
byte still blocks that stream; a full stereo frame needs both eyes. A stream per
frame plus expiry/reset is an alternative to FEC, subject to flow/stream limits,
recovery and shared congestion. A single reliable media stream retains ordering
across frames. Neither QUIC alternative is implemented by this reference.

https://www.rfc-editor.org/rfc/rfc8684.html
Multipath TCP is another research option for multiple interfaces, but its reliable
connection-level byte stream does not by itself implement obsolete-frame
abandonment. Deployment/kernel support, path scheduling and reordering cost need
separate evidence. It is not a direct cure for this stereo freshness constraint.

https://man7.org/linux/man-pages/man2/sendmmsg.2.html
https://man7.org/linux/man-pages/man2/recvmmsg.2.html
Batched UDP syscalls are a separate CPU-overhead experiment. Batching must remain
bounded and deadline-aware; waiting to fill batches can add latency. Partial sends
and nonblocking receive drain must be handled. No batching speedup is measured.
