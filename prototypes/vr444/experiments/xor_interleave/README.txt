VXI1 interleaved XOR groups (2026-10-09)

Decision
Keep a separate offline branch. Interleaving recovers consecutive source losses
using the same number of parity records as VXF1, but some periodic losses and
late-parity cases favor contiguous stripes. These are deterministic test cases,
not observed network reliability. No sockets, headset operations, deployment,
authentication, congestion controller or latency improvement are demonstrated.

Wire and bounded implementation
VXI1 is a separate magic/version profile, not a compatible change to VXF1. All
44-byte header fields, manifest, CRCs, two manifest copies, 1,152-byte shard size,
1,196-byte UDP payload limit, deadlines and bounds are specified by
../datagram_xor/README.txt. No extra metadata or parity is introduced. Both
receivers reject the other magic before allocating candidates. Negotiation is
not implemented. CRC/parity are not authentication or tamper protection.

Let N=ceil(pair_bytes/1152), K in {4,8,16}, and G=ceil(N/K). Interleaved group g
contains source indices g,g+G,g+2G,... below N. Every source appears exactly once,
and each group has at most K sources. XOR pads the final partial source with zero;
recovered padding must be zero and both final eye CRCs must match. Width0 disables
parity. The source order remains left||right in ascending source index. Each
group's parity is emitted immediately after its last source; this is the earliest
availability without changing source order. Parities consequently occur toward
the end of a pair, unlike contiguous VXF1 stripes. No cross-frame history exists.

Any consecutive run of at most G source-only losses has at most one missing
source in each group. With manifest and corresponding parity received before
expiry, all are recoverable. A wire burst may also erase parity/manifests, so it
does not have this unconditional guarantee. Two losses separated by G fall in
the same group and cannot be recovered by its single parity shard. Interleaving
does not increase code distance or recover arbitrary G erasures.

Receiver code is inherited from VXF1 after extracting only a wire_magic class
constant and group_indices hook. Existing VXF1 packetizer bytes are unchanged.
Receiver-issued tickets, session/config/warp identity, two pending candidates,
64KiB orphan cap, 655,704-byte buffered payload cap, bounded retirement, fresh
post-assembly clock check and complete stereo publication remain in force.
The sender holds at most 57 XOR accumulators, each at most 1,152 bytes. Python
object overhead is not included in those payload bounds. No production path is
wired to this reference. The 256KiB pair cap cannot hold arbitrary raw RGB pairs;
over-cap inputs reject and need an independently specified transport profile.

Reproduce from repository root
  python3 prototypes/vr444/experiments/datagram_xor/test_transport.py
  python3 prototypes/vr444/experiments/xor_interleave/test_transport.py
  python3 prototypes/vr444/experiments/xor_interleave/matrix.py /tmp/vxi1-matrix.json
  python3 prototypes/vr444/experiments/xor_interleave/summarize.py /tmp/vxi1-matrix.json /tmp/vxi1-summary.json

Correctness and modeled evidence
24 existing VXF1 tests and 11 VXI1 tests pass. VXI1 checks every N=1..228 with each
K for partition, maximum group size and consecutive-erasure geometry. Actual
recovery covers partial/exact shard boundaries, 256KiB, every single wire loss,
burst positions, duplicate reordering, first-manifest loss, no-parity loss,
cross-profile rejection, malformed header/CRC/identity, CRC-repaired corrupt
parity including nonzero recovered padding, expiry and previous-pair retention.

matrix.py executes 2,808 cases in 1,404 matched comparisons: four source sizes,
three widths, three modeled service rates and 39 erasure scenarios per layout.
Rates are 50/100/300Mbps with 28 IPv4/UDP bytes added to each datagram. Service
time is rounded up per packet; erased packets consume service. The ticket is
50ms; the late-parity scenario adds 40ms to every parity arrival. Source bursts
erase source indices; wire bursts erase record ordinals including parity and
manifest copies. Both layouts have exact equal counted wire bytes and source
hashes. Every successful publication is exact and before expiry; bounds are
checked after each record. No propagation, WiFi/MAC, jitter, CPU time, congestion,
ACK/control or encryption is modeled. First-record-to-stereo is a model interval,
not the live earliest-receive-to-post-xrEndFrame gate or optical latency.

Matched outcomes                       both pass  both fail  VXF only  VXI only
consecutive source-only erasures            114        339         0       195
consecutive wire-record erasures            148        434         0        66
clean                                       34          2         0         0
periodic losses at indices0,G                 0         23        13         0
source0 lost and all parity40ms late         28          2         6         0

These cases are deliberately constructed and not weighted by any measured loss
distribution; their counts must not be interpreted as reliability percentages.
The two clean failures are 256KiB,50Mbps profiles whose FEC wire service exceeds
the50ms ticket. Neither layout solves overload by adding redundancy.

Example: 256KiB,K8 at modeled100Mbps has N228,G29,259 records and 307,036 UDP
application bytes (314,288 with assumed IPv4/UDP headers), identical in both arms.
Erasing the first eight sources: VXF1 fails, VXI1 completes exactly at25.045120ms
from generation. Erasing indices0 and29: VXF1 completes, VXI1 fails. Losing source0
and delaying parity40ms: VXF1 completes at40.899840ms while VXI1 expires. Clean
completion is25.045120ms for both. The last unnecessary parity can arrive after
publication. These are schedule calculations, not processing-time measurements.

results/model.json keeps every modeled outcome plus source/accounting summaries
and a SHA256 of the full reproducible raw result. results/provenance.json records
source hashes and commands. No captured image content is used in this branch.

Primary source consulted2026-10-09
https://www.rfc-editor.org/rfc/rfc5109.html
RTP ULPFEC describes XOR protection groups. This experiment uses independent
framing and deterministic interleaved membership, and does not implement RTP
ULPFEC interoperability. Production redundancy still needs congestion control.
