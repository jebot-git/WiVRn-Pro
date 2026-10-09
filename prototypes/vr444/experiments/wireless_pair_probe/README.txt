Explicit-peer TCP / UDP complete-pair diagnostic
==============================================

This NEW isolated harness is a physical-path experiment only when its separately
launched endpoints actually use that path. Source compilation, grammar tests,
ADB reachability and loopback runs do not establish wireless behavior. Root task
owns endpoint deployment and execution; no socket has been opened during this
branch's implementation/grammar qualification. Historical transport prototypes
udp-batch3075ff7c, udp-offload74e678a1 and codec-udp-interopf1fe2e43 are all hard-bound
to localhost and cannot be relabeled as wireless measurements.

The test compares ordinary TCP (TCP_NODELAY) and ordinary UDP, using identical
complete opaque synthetic stereo pairs. It does not implement production VR444,
VXF/FEC, UDP batching, GSO/GRO, OpenXR, video timestamps or native image ownership.
TCP and UDP results can select a physical-path question for further development,
not qualify any of those unimplemented adapters. No real content is transmitted.

Bounds and lifetime
-------------------
Each endpoint requires an explicit local unicast IPv4 address, explicit expected
peer IPv4 address, dedicated high port49152..65535, identical nonzero128-bit run
token,1..180pairs,2..262144bytes per complete stereo pair and5..90Hz. Nominal
configuration duration is at most8s and counted IPv4+UDP data+ACK offered load is
at most100Mbps (only from the explicit CLI size/rate arguments). Default suggested run is60pairs x32768bytes at20Hz (~5.57Mbps),
nominal3s. No root privilege, route/sysctl/service change or arbitrary listener
address0.0.0.0 is used. The receiver closes after its finite run; sockets are RAII
closed on every return/exception. The receiver retains one private pair plus one
<=1200-byte lookahead record. Previously completed pairs are represented only by
small bounded timing/hash rows, never an accumulating image history.

Source first half is left and second half right, generated from the token, frame
ID and independent eye domains. Both are regenerated independently by the receiver.
Every packet is shape/identity checked, every payload byte must equal the expected
byte, duplicate bytes cannot advance completion, and only a complete exact pair
before the fresh100ms receiver deadline receives a complete ACK. A dropped or
corrupt right eye cannot pass. Token and expected-peer checks isolate accidental
cross-run traffic; they are NOT cryptographic authentication. Plain diagnostic
traffic has no confidentiality, negotiated control, congestion protocol or retry
for UDP. Packet/body corruption is detected by exact deterministic byte validation.

WPP1 DATA is a40-byte little-endian header plus1..1160bytes, maximum1200bytes:
magicWPP1, version1, kindDATA1/ACK2, reservedu16zero, token16bytes, frameu32,
whole-pair-bytesu32, indexu16, record-countu16, body-lengthu16, reservedu16zero.
Canonical record-count is ceil(pair_bytes/1160), index starts0 and final length
is exact. ACK body is16bytes: receiver assembly nanosecondsu64, complete countu32,
complete pair bytesu32. TCP prepends a2-byte record length; its partial byte I/O
loops preserve framing. UDP preserves individual records and does not retransmit.
The receiver never publishes a partial pair. CRC/parity are not used or claimed.

Timing, pacing and accounting
-----------------------------
Sender pre-generates each pair outside the clock, sends its complete records and
waits for its complete ACK before the next pair. It maintains the requested
minimum interval between starts, with no catch-up burst after delays. This is
stop-and-wait with one pending pair, not overlapping90Hz native video. All socket
I/O is nonblocking with bounded poll and absolute deadlines. Sender100ms clock
starts before first DATA send; TCP expiry terminates the connection to avoid
stream desynchronization. Receiver100ms clock starts at first userspace receipt
of a valid-shaped DATA record for this pair (the first TCP record prefix byte,
or the first UDP datagram; payload corruption still starts this clock), and
stops only after complete byte equality. Receiver expected-pair generation is
before first receive and excluded from assembly time (sender RTT may include it).
Diagnostic row hashes and ACK send follow the assembly timestamp, still before
the same fresh deadline. The100Mbps ceiling is nominal IPv4 UDP offered accounting,
not instantaneous burst pacing or a physical/TCP wire cap. There is a30s run budget,
5s initial connection/record wait and500ms subsequent idle wait. No measured
one-way host-to-headset duration, clock synchronization or optical latency exists.

Sender RTT includes outbound service, receiver assembly/validation, ACK return
and scheduling. Receiver assembly is on the receiver's monotonic clock. Missing
ACKs and incomplete receiver pairs stay in the counts; report both endpoints to
distinguish delivery failure from ACK loss. P50/p95 of successful pairs alone
cannot qualify a transport with worse completion/cadence. Neither metric equals
the production earliest-receipt-to-post-xrEndFrame metric.

application_bytes_sent/received counts actual successful socket bytes, including
WPP framing and TCP2-byte prefixes. UDP counted_ipv4_udp_bytes_sent additionally
adds28bytes per successfully sent datagram, including ACKs at the receiver.
TCP physical wire bytes are explicitly unknown: segmentation, ACKs and kernel
retransmissions are not observed. UDP link-layer/WiFi retries, encryption and
MAC overhead are also not measured. No reported count is physical packet-capture
wire evidence. Socket buffer capacities are queried, never enlarged.

Build and pure offline checks
-----------------------------
  bash prototypes/vr444/experiments/wireless_pair_probe/build.sh
Builds host and Android API29 ARM64. Only the separate ASan+UBSan grammar/assembly
executable is RUN: it opens no sockets. The probe itself is not invoked here.
The compiled Android test is likewise pure grammar/assembly and may be run before
any endpoint test by the root coordinator. Full syscall interoperability remains
untested until the authorized endpoint run.

Root-owned bounded live recipe (substitute observed local/peer addresses)
------------------------------------------------------------------------
Generate a fresh32-digit random token for EACH matched TCP/UDP comparison and retain it in the
plan. Select one unused dedicated port such as53171. Start the receiver first
then sender promptly; do not overlap production latency or other device tests.

  # On Quest, via the coordinator's exact verified binary deployment:
  probe-android receive udp QUEST_IP HOST_IP 53171 TOKEN 60 32768 20
  # On host, same token/config, within5seconds of receiver readiness:
  probe send udp HOST_IP QUEST_IP 53171 TOKEN 60 32768 20

Repeat withtcp after both previous processes have exited, using a fresh token and
port. For matched opaque content, token must stay identical across the TCP/UDP
arms while ports isolate them; use a new shared token for each AB/BA repetition.
Alternate transport order. Explicitly hash binaries and retain both stdout/stderr,
exit statuses, exact args, input rate, device/network identity and observation times.
A minimal initial check is5pairs32768bytes20Hz. Only after it passes use60pairs.
Then, if admitted by the coordinator, compare180pairs x98304bytes at90Hz
(~75Mbps nominal) with the SAME100ms deadline and explicit size/rate arguments.
This is a two-second nominal target-load burst, not sustained production video.
Local sender-start and receiver-completion offsets report achieved event cadence;
stop-and-wait can lower it below the requested90Hz and that failure stays visible.
Cadence spans first/last observed events and is not a full-window freshness gate.
Sustained pacing, broad loss distributions and FEC still need later evidence.

CPU branch choice alongside this diagnostic
------------------------------------------
The existing hash-bound rowblocks-workers260a1a22 Android benchmark compares
whole stereo VR444/RRF/RRB32/RRB128 and serial/2/4workers. Use its CLIENT clock
and process CPU fields for Quest; on-device server/combined fields are not the
actual desktop server. audit_worker.py rechecks full collected output/plan receipt,
source cases, canonical bytes and every31-sample quantile before summarizing.
The three predetermined capture pairs are a directional target observation.
At90Hz, process-CPU-ms *9 is nominal percent of one core; it is not measured
utilization, thermal headroom, affinity or sustainableVR frame rate.
