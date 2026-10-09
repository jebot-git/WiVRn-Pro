VWG1 bounded packet-window repair over VXB1
=========================================

This independent approach addresses VXP's measured byte-cost problem: many
small RRF blocks contained fewer source fragments than a nominal XOR group.
VWG groups complete VXB records across block/eye boundaries instead. Source
records can be emitted immediately; a parity record follows each K records,
or the final partial group when finish() is called. It does not wait for all
stereo block lengths, and it does not change RRF/RRB pixels or GPU APIs.

This is still an offline Python protocol prototype and a native exact-pixel
oracle. No sockets, headset, production transport, integrated worker/network
overlap or performance measurement is supplied. Its compact handle negotiation
is a new explicit requirement, not an existing production capability.

Wire format and identity
------------------------

24-byte little-endian outer header: <4sBBBBHHQI>
  magic VWG1, version 1, kind (DATA=1, PARITY=2), K (4/8/16), slot u8,
  group u16, count u16, receiver-issued handle u64, CRC32 u32.
CRC covers the first 20 header bytes plus body. DATA count is zero and slot is
the record index within the group. PARITY slot is zero and count is the exact
1..K protected population. K is immutable within the frame. Source positions
and group IDs are bounded before state creation. Each DATA body is one complete
original VXB record, at most 1176 bytes; outer UDP payload is at most 1200 bytes.

The receiver issues a nonzero 64-bit handle for an exact frame/warp deadline
within its current full session UUID and session/configuration epochs. At most
32 live handles/tickets exist; one frame's handle/warp/deadline cannot change.
Unknown, expired, published or retired handles do not create repair state.
Configuration changes clear handles and candidates. The handle counter does
not restart within the receiver, and exhaustion fails closed. This is a compact
identity reference, not a secret or authentication mechanism.

Every original and reconstructed inner record must pass the full VXB header,
CRC, epoch/frame/warp identity, canonical fragment fields, and manifest
UUID/issued-deadline/shape checks before it is passed to the VXB receiver.
The inner receiver retains its strict RRF grammar, complete block CRC, canonical
RRB assembly, both-eye admission and fresh final deadline checks. A repaired
record alone never publishes a partial eye or image.

XOR applies to the complete inner records, including headers. Short records are
zero-padded to the longest group member. The recovered inner header gives its
exact original length; every remaining padding byte must be zero. Parity length
must equal the maximum complete source-record length. Once a known parity window
has at most one missing record, its parity, recovered inner grammar and canonical
length are checked BEFORE either an original or recovered record can complete
stereo. The missing record may only duplicate a manifest, so complete inner eye
bytes alone do not excuse malformed, already-checkable parity. Both final-source
and final-recovery negative tests protect this publication ordering.

One missing record per group is recoverable; two missing codec DATA records in
one group are not. Complete K-source groups can discard their stored source
copies without waiting for parity. Sealed partial groups discard storage when
complete or repaired. A bounded completed-group set ignores later duplicates.
The sender uses only a partial final group; the receiver validates any explicitly
sealed short group's population. No claim of authentication or resistance to
malicious CRC-consistent content is made.

Bounds and ownership
--------------------

The underlying canonical stereo cap remains 256 KiB and block count remains at
most 512 across both eyes. A conservative source-record count bound is
  ceil(262144/1120) + 512 + 2 = 749.
At least four records fit a group, so no frame has more than 188 group identities.
Complete inner source bytes are bounded conservatively by
  262144 + 56*749 + 2*96 = 304280 bytes.
Each parity body is no larger than its group's summed source bytes. Retained
source+parity window payload is capped at twice that bound, 608560 bytes/frame,
before insertion or recovered-record storage. At most two wrapper frames exist;
the VXB receiver also retains at most two candidates. The combined conservative
retained payload bound is
  2 * (608560 + 262144 + 40) = 1741488 bytes.

This is not peak RSS. Temporary XOR/join/canonical copies, previously published
pairs, caller-owned source records and Python metadata are additional. Metadata
counts remain bounded by handles, groups, slots and inherited trace limits.
The inner 64 KiB orphan cap and 20 ms orphan TTL still apply to source fragments
before their VXB manifest. Outer repair groups already have a receiver-issued
identity handle and may remain until that handle's at-most-100 ms deadline;
they are not mislabeled as part of the inner orphan budget.

Source records are immutable bytes. A sender error, incomplete finish, duplicate
source block or later canonical-budget overflow permanently aborts the sender.
Already emitted data cannot publish an incomplete pair and expires normally.
On a fully delivered pair, publication can precede trailing parity; offered-wire
accounting still includes every parity record. Calling finish is required to
provide recovery for an incomplete final group, not to make intact inner stereo
data suddenly valid.

Qualification and limits
------------------------

Run `python3 -m unittest -v test_transport.py` from this directory. qualify.py uses
the same manifest/export/corpus/build-receipt arguments as rowblock_stream, plus
a new output path. All 38 exported eyes, four row sizes and K=4/8/16 are exercised.
Twelve capture pairs preserve their eye identities; fourteen synthetic profiles
are explicitly duplicated across eyes. Returned canonical RRB bytes must equal
the originals, and the unchanged bound ASan/UBSan native oracle independently
checks original VR444 decode, exported RGBA and both reconstructed RRB eyes.

Qualification compares actual offered UDP bytes with both VXB no-recovery and
VXP per-block repair at matching row/K settings. Header overhead is included.
It also erases matching codec-fragment identities from the two repair formats:
lengths 1/2/4/8 starting at the beginning, middle and end of their checked common
codec DATA order. Manifests and all parity are retained. Every successful result
must still equal the original stereo bytes. These are matched source erasures,
not equal contiguous on-wire bursts, a random-loss distribution, or recovery
probabilities. Completion counts therefore apply only to this declared matrix.
It does not infer 1/K overhead, physical loss probabilities, retransmission,
congestion behavior, native packet handling cost or headset capability. Larger
windows can delay repair and couple losses across blocks; lower byte cost alone
does not establish lower latency. No timing or end-to-end promotion follows.

The general XOR repair principle is described in RFC 8627:
  https://www.rfc-editor.org/rfc/rfc8627.html
VWG1 is not its RTP wire format and claims no standards interoperability.
Q8 reconstruction preserves previously decoded bytes, not original-input
losslessness. No headset launch is authorized by these checks.
