Bounded UDP segmentation / receive aggregation experiment
=========================================================

Status: isolated owned-localhost research. This branch inherits udp-batch@3075ff7
and compares the same sendmmsg/recvmmsg baseline with Linux UDP_SEGMENT (GSO) and
UDP_GRO. The baseline timed implementation is unchanged; only its standalone main
is guarded so the offload probe can reuse it. No production socket, route, sysctl,
interface, listener, headset, ADB connection or application setting is changed.

Three profiles and exact boundaries
----------------------------------
1. sendmmsg_recvmmsg: existing baseline, same bounded burst schedule.
2. gso_recvmmsg: each suitable group is one sendmsg with UDP_SEGMENT ancillary
   data; receive ordinary separate datagrams with recvmmsg.
3. gso_gro_split: same sender; owned receiver explicitly opts into UDP_GRO.
   recvmsg consumes an aggregate with its segment-size metadata, then bounded
   splitting/copying reconstructs individual application packets.

No input packet is padded or rewritten. A GSO group consists of equal-size
packets with at most one shorter final packet. A following larger packet starts
a new group. Mixed framing-shaped inputs include 88-byte manifest-like records,
1,196-byte payload-like records, and 61/83-byte tails. These are synthetic packet
bytes, not a VXF1 end-to-end integration. Complete multiset byte/count equality is
required after each iteration. Reordered datagrams are permitted; loss, added,
duplicated or changed packets cannot pass the exactness oracle.

At most 512 input packets, 1..1,200 bytes each, and 32 already available packets
per burst. There is no deliberate wait-to-fill. At most 38,400 payload bytes per
GSO send or GRO receive, below both the 64-segment Linux API limit and normal UDP
aggregate size bound. Both sockets bind to ephemeral 127.0.0.1 ports owned by
this process. The sender connects only to that owned receiver. Every received
peer address/port and byte count is checked. Input/receive storage is allocated
before timing; variable-size grouping and its allocations, control construction,
syscalls, GRO parsing and split/copy work are INCLUDED in timing.

Nonblocking syscalls handle EINTR/EAGAIN with an absolute 100 ms iteration budget
and at most 5 ms poll intervals. GSO sendmsg must accept the whole aggregate;
there is no partial datagram retry. recvmmsg handles partial record counts.
GRO metadata must be one correctly sized IPPROTO_UDP/UDP_GRO integer; unknown,
duplicate, truncated or oversized control records reject. Aggregate count and
segment/tail sizes are bounded before copying. No control metadata means an
ordinary single <=1,200-byte packet. MSG_TRUNC/MSG_CTRUNC reject. The probe closes
owned sockets on failure; it does not switch transports midway through a frame.

Capability and measurement boundaries
-------------------------------------
Host support is tested using only newly owned loopback sockets. Known unsupported
option errors are reported explicitly; unsupported profiles are omitted rather
than silently replaced. Successful compilation is not runtime support. The API29
arm64 binary is cross-compiled only, never installed or executed on a headset.

These APIs are Linux-specific and may use software segmentation/coalescing.
Loopback can carry a large packet internally and split only at the receiver; it
does not demonstrate physical NIC, WiFi driver, IP/UDP offload or target runtime
behavior. Segment payload size must still obey actual path-MTU rules; this probe
does not discover a path MTU or change fragmentation settings. No congestion
controller, pacing, reliable recovery, encryption/authentication, session identity
or stereo publication is introduced. Offload is an optional socket optimization,
not a replacement for those transport contracts. CRC is not authentication.

The metric is one-thread whole-loopback work, with input/socket setup and exact
postcheck outside timing. This is not transfer latency between separately
scheduled endpoints, receive-to-submit, compositor or optical latency. No sockets
are deliberately pressure-loaded: natural EAGAIN/poll count was zero in the
frozen measurement. EINTR/EAGAIN handling remains code-covered by inspection and
the inherited helpers, not a naturally exercised performance claim.

Reproduce
---------
  prototypes/vr444/experiments/udp_offload/build.sh
  /tmp/wivrn-udp-offload-build/test
  prototypes/vr444/experiments/udp_offload/build-android.sh
  /tmp/wivrn-udp-offload-build/probe 31 > /tmp/udp-offload.json
  python3 prototypes/vr444/experiments/udp_offload/summarize.py \
    /tmp/udp-offload.json /tmp/udp-offload-summary.json

Build uses existing clang and Android NDK; no package installation. ASan+UBSan
passed 232 roundtrips across single/partial/full bursts, all boundaries near
32-record windows, 17/64/1196/1200-byte inputs, mixed sizes, short final segments,
and 19 malformed/control/source/bounds rejection checks. Both offload profiles
are supported on this host. Owned alternate-source and oversized-record tests
verify failure containment, not remote network security.

Frozen comparison: 32 matched cases x 3 profiles x 31 repetitions, plus 3 warmups;
deterministic shuffled case order and rotating profile order. Individual samples,
capability observations, per-iteration call counts and source/binary hashes are
recorded in results/. GPU/compositor collaborators explicitly held workloads for
the brief run. Ordinary desktop/services continued; no affinity, frequency or
thermal control. Do not combine this run with different-clock/device measurements.

Representative host p50 milliseconds, including group planning and GRO splitting:

  Input                 burst  mmsg base  GSO+mmsg  GSO+GRO
  35 x 1200                1   0.062217   0.062457  0.062255
  35 x 1200               32   0.054912   0.017835  0.007232
  228 x 1200               1   0.409048   0.413072  0.410706
  228 x 1200              32   0.356539   0.108636  0.040321
  228 mixed-size records  32   0.363832   0.112652  0.044161

At 228 x 1200 / burst32, GSO+GRO saves 0.316218 ms (88.7%) in this host loopback
microbenchmark. All three profiles make eight send and eight receive calls: the
difference here is kernel per-datagram work, not simply syscall count. GRO returns
up to 32 exact segments. With burst1 there is no useful improvement. Preserve as
a capability-gated candidate; these sub-millisecond host savings cannot establish
the several-millisecond improvement required by current strict headset evidence.

Primary API reference
---------------------
Linux man-pages udp(7), UDP_SEGMENT / UDP_GRO options and path-MTU behavior:
https://man7.org/linux/man-pages/man7/udp.7.html
