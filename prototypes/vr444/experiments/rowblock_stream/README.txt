VXB1 independently completed row-block transport experiment
==========================================================

Status: offline prototype. No sockets, headset, production transport integration,
incremental GPU reconstruction, timing comparison, or latency promotion.

The existing fused server workers can finish independent RRF blocks before a
whole RRB stereo pair is assembled. VXB1 explores the corresponding framing:
Sender.add_block accepts any independently completed block without requiring the
lengths of later blocks. The receiver validates and privately retains complete
blocks as they arrive. It only returns a pair after all blocks of both eyes have
passed validation and canonical RRB assembly has completed before the deadline.
Neither partial eyes nor blocks are externally published by this prototype.

This is a distinct wire version. It does not change VXF1/VXI1 or production
VR444. It currently has no FEC, retransmission, congestion control, authentication,
encryption, negotiated capability exchange, or runnable overlap scheduler.
All CRCs detect accidental corruption; they are not authentication.

Wire profile
------------

Each record uses the little-endian 56-byte structure
  <4sBBBBIIQQHHHHIIHHI>
Fields in order: VXB1 magic, version 1, kind, eye, zero reserved byte,
session/configuration epochs, frame/warp IDs, block index, blocks per eye,
shard index/count, complete block length/CRC32, record body length, zero reserved
u16, CRC32(first 52 header bytes followed by body).

DATA kind 2 uses eye 0/1. Its fragment body is exactly 1120 bytes except the final
fragment. Thus the largest UDP payload would be 1176 bytes. A complete RRF block
is reconstructed from its independently indexed fragments. Length, CRC, index,
count, and shape must agree across the complete candidate. A CRC-valid conflict
retires the candidate instead of choosing one interpretation.

MANIFEST kind 1 uses eye 255 and zero block/fragment/length/CRC fields. Its 40-byte
body is <16sQIIHHI>: session UUID, receiver-issued deadline, width, height,
block rows, blocks per eye, zero reserved u32. Total manifest record is 96 bytes.
The first add_block emits manifest, first DATA, duplicate manifest, remaining
DATA. Every later call emits only DATA. Dimensions and block size are known at
start; subsequent block lengths need not be known. Both eyes must share this
profile. RRB's more general different-per-eye block sizes are not admitted here.

Axes are at most 4096, pixels per eye at most 4,194,304. Block rows are exactly
16/32/64/128, giving at most 256 blocks per eye. Every complete RRF block retains
the original raw/plain-LZ4/up-LZ4 mode and validates full body/header CRC, exact
dimensions, canonical lengths, and strict LZ4 grammar (including nonzero offset,
bounded references, exact output, final literals and last-match restrictions).
The reconstructed RRB header/table/body is byte-identical to the original
canonical RRB input; no pixel or quantization change is made. In particular,
preserving decoded Q8 bytes does not make the original Q8 input lossless.

Bounds and lifetime
-------------------

The reconstructed stereo pair is capped at 256 KiB. A candidate reserves
  64 + 16 * blocks_per_eye + sum(unique declared complete block lengths)
before accepting a newly described block's fragment. This includes both RRB
headers/tables. The receiver never relies on a future complete block to discover
that the pair's declared byte budget is exceeded.

At most two candidates, 32 receiver-issued tickets, 128 retired identities,
4096 trace records, 64 KiB of globally retained orphan payload, and a 20 ms
orphan TTL are inherited from datagram_v2. Deadlines are receiver-local, issued
for an exact (frame, warp) identity, and at most 100 ms ahead. Neither sender time
nor a replay's offered time can extend that ticket. A fresh receiver clock check
follows each block validation and final stereo assembly. Session/configuration
changes clear candidates and tickets. Already published/retired frames cannot
return to the candidate set.

MAX_BUFFERED=524368 bytes describes retained pending payload (two 256 KiB pairs
plus two 40-byte manifest bodies). It is not peak RSS or an allocation bound:
joined block copies, canonical assembled eyes, a previously published pair,
source records, return values and bounded Python dictionaries/trace metadata can
coexist. No statement about OS/device resident memory follows from this counter.

Streaming before knowing total size introduces an explicit admission tradeoff:
a later block may exceed the remaining stereo budget. Sender.add_block then
permanently aborts that frame; already emitted data does not make it publishable,
and the incomplete receiver candidate expires. There is no optimistic partial
display or implicit fallback packet under the same identity. Whole-pair
packetize can reject the complete oversized pair before emitting anything.

Arbitrary packet reordering is bounded by orphan capacity and TTL. The corpus
shuffle retains the first manifest; the small independent golden tests also
exercise fully reversed and completely shuffled arrivals within orphan bounds.
A lost DATA record causes whole-stereo withholding in this no-recovery profile.
The duplicated manifest tolerates one manifest loss, not missing source data.

Qualification
-------------

From this directory:
  python3 -m unittest -v test_transport.py
  python3 build.py --out /tmp/wivrn-rowblock-stream-build
  python3 qualify.py --manifest /tmp/sub15-rgb-rowblocks-gpu-corpus-manifest.json \
    --export /tmp/sub15-rgb-rowblocks-gpu-corpus \
    --corpus /home/blux/spatial-bridge/validation/results/codec-viability-0.8.0/compact/corpus \
    --build-receipt /tmp/wivrn-rowblock-stream-build/build-receipt.json \
    --out /tmp/sub15-rowblock-stream-qualified.json

The export contains 24 actual captured eyes and 14 synthetic eyes, each with four
row-block sizes. Actual left/right capture identity is preserved. Each synthetic
profile is explicitly duplicated across both eyes, not relabeled as a capture.
The native ASan/UBSan oracle independently decodes original VR444, checks the
exported RGBA oracle, then decodes the exact reconstructed RRB pair with the
bounded stereo workers and compares every pixel of both eyes. Native success is
printed only after both eyes pass. The build receipt binds compiler commands,
sanitizers, compiler-discovered local dependencies, source hashes and binary hash;
qualification rechecks all corpus, source, binary, manifest and receipt hashes.

Logical arrivals advance by one microsecond per record. These are correctness
inputs, not a measurement or model of real network/decoder service. Any future
overlap timing experiment must include actual worker completion and queueing,
all framing/serialization, first-receipt timing, complete stereo reconstruction,
admission failures, and publication. No speedup follows from this wire prototype.
