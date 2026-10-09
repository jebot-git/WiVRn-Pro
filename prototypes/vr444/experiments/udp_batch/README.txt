Bounded native UDP batching experiment (2026-10-09)

Scope
This tests a concrete alternate syscall path for an eventual datagram transport.
It is a standalone C++ harness with owned loopback sockets only, never production
networking or headset/ADB work. There is no WiVRn integration, stereo assembler,
codec, QUIC/DTLS authentication, congestion control or WiFi emulation. Payloads are
deterministic opaque test records, not a proposed interoperable wire format.

Build and run
  bash prototypes/vr444/experiments/udp_batch/build.sh /tmp/udp-batch
  /tmp/udp-batch/test self-test
  /tmp/udp-batch/probe 31 > /tmp/udp-batch.json
Optional offline Android API29 arm64 cross-build, no deployment/run:
  bash prototypes/vr444/experiments/udp_batch/build-android.sh /tmp/udp-batch

Both sockets bind explicitly to127.0.0.1 on ephemeral ports. The sender connects
only to the owned receiver. Socket descriptors are nonblocking/close-on-exec and
RAII-closed on success or failure. No external address, routing, root privilege,
network setting or service is used. The existing receive-buffer setting is queried
and reported, never changed. Resource use is bounded: <=512 input records,
1..1200 bytes each, <=32 outstanding records, and one100ms monotonic deadline per
iteration. The normal matrix uses at most228 records. Failure aborts the result.

Profiles and timing
Single-call profile uses sendmsg/recvmsg once per record. Batched profile uses
sendmmsg/recvmmsg with a maximum of1,4,16 or32 records. Both use the SAME burst
schedule: send up toK already available records, then drain thoseK before starting
the next window. The final partial window is sent immediately. The harness never
waits for a future frame/record to fill a batch. This is deliberately a one-thread
syscall/loopback comparison, not independently scheduled endpoints.

The24 matched cases combine record counts2/35/228, sizes64/1200 and windows1/4/16/32;
each has two profiles, three warmups, and caller-selected5..100 repetitions.
Profiles rotate order each repetition and case order is seeded/shuffled. Source
generation, socket creation, receive-buffer allocation and exact multiset content
checking are outside timing. Iovec/message setup, all send/receive work, return
validation, address/truncation checks and any EAGAIN/poll wait are inside. The
whole_loopback_iteration_ms interval starts before the first send and ends after
the final receive. It is NOT earliest-headset-receive-to-submit, network-only
latency, throughput capacity, GPU time, stereo freshness or optical latency.

Every record is checked byte-for-byte as an exact multiset after the measured
interval, including duplicate multiplicity. The benchmark requires complete
receipt; it does not conceal loss by publishing a subset. Real application stereo
identity, deadlines and integrity still need the separate bounded assembler.
The harness currently makes no retransmission or packet-drop policy comparison.

Partial results and timeouts
sendmmsg may return after only some messages; ownership/offsets advance only by
the returned count. recvmmsg is treated the same way. Both retry EINTR and handle
EAGAIN using poll in <=5ms pieces, rechecking the absolute100ms deadline. No
blocking recvmmsg timeout is used: MSG_DONTWAIT plus explicit poll handles idle
expiry. A short UDP send, unowned source address/port, truncation/control truncation,
empty/oversized record, poll failure or exact-content mismatch aborts the iteration.

Validation includes ASan+UBSan, all window sizes, a35-record partial final batch,
forced send/receive caps of2/3 records exercising the partial-result retry loops,
empty-queue EAGAIN/expiry, missing/corrupt record comparison, malformed input
sizes/counts, a deliberately wrong owned-local source port, and an oversized local
packet. Forced caps are test injection, not evidence that partial syscalls occurred
naturally in the performance run. Android build success does not establish target
kernel behavior or performance. There is no headset timing in this experiment.

Primary references, consulted2026-10-09
https://man7.org/linux/man-pages/man2/sendmmsg.2.html
The syscall can return a partial message count; later errors need explicit
handling. The benchmark retains unsent record ownership until successful sends.
https://man7.org/linux/man-pages/man2/recvmmsg.2.html
Nonblocking calls return currently available messages. The documented timeout
caveat makes an explicit absolute deadline/poll preferable for this bounded
prototype. Batch processing can reduce syscalls; waiting to accumulate a batch
could instead increase latency, so this harness only sends available records.
