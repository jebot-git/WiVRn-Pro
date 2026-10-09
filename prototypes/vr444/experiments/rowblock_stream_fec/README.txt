VXP1 independent-row-block XOR recovery experiment
=================================================

Separate experimental wire format built on the checked VXB1/RRF/RRB byte
contract. It does not alter production networking, codec bytes, or GPU APIs.
This is Python logical replay plus an independent native pixel oracle: no
sockets, headset execution, measured timing, or integrated overlap scheduler.

The motivation is to preserve block-level availability while recovering one
missing source fragment per short XOR group. The underlying XOR repair principle
is described in RFC 8627 (https://www.rfc-editor.org/rfc/rfc8627.html), including
the distinction between source and repair packets. VXP1 is an independent
prototype; it is not the RFC's RTP format and claims no RTP interoperability.

Framing and recovery
--------------------

The 56-byte little-endian VXB structure and 40-byte manifest body remain the
same shape, but magic is VXP1, version is 1, and header byte 7 (the former zero
reserved byte) is K=4,8,16. K is checked on every record and is immutable within
the candidate, including parity received before the manifest. Old VXB magic
is rejected rather than interpreted as the repair profile.

Kinds are manifest=1, DATA=2, PARITY=3. DATA's index remains its source shard
index. PARITY's index is the consecutive group index g; source members are
[g*K,min((g+1)*K,source_shards)). Both kinds carry the same exact source block
length, CRC, source shard count, eye and block identity. These descriptors must
agree across all records before any recovery. Groups never cross block or eye
boundaries. Independent blocks can be emitted in any order.

For each group, XOR its source body bytes with zero padding to the longest
member. Parity length is exactly min(1120,block_length-g*K*1120), so a group
containing only a short final source uses equally short parity. A PARITY record
follows the last DATA of each group. The duplicate manifest still follows the
first DATA in the underlying sender; for a one-source first group, its PARITY
appears before that duplicate manifest. Largest record remains 1176 bytes.

Exactly one missing source plus the matching parity recovers a private fragment.
Two missing sources in the same group remain incomplete. If the missing source
is short, every recovered padding byte past its known length must be zero.
Complete-source parity can be checked by recomputation. Recovered data still
passes the entire block CRC, RRF header/body CRC, strict LZ4 grammar, exact shape,
canonical RRB assembly and fresh whole-stereo deadline checks before publication.
CRC-valid inconsistent parity can retire the candidate; CRC/XOR are not
authentication and provide no adversarial integrity guarantee.

Neither partial pixels nor repaired fragments become public images. No parity
is shared across frames, configurations or sessions. A stale/expired ticket is
not extended by recovery. Later sender budget failure still permanently aborts
the frame. The existing row-block and native stereo lease semantics are unchanged.

Bounds and cost
---------------

The canonical stereo cap remains 256 KiB, reserved from all unique declared
source block lengths before accepting fragments or parity. At most two candidates
exist. Every group's parity length is no greater than the sum of that group's
source lengths; therefore total retained parity for a candidate is at most its
declared source payload. MAX_BUFFERED is conservatively
  2 * (2 * 262144 + 40) = 1048656 retained pending payload bytes.
The global 64 KiB orphan cap includes source AND parity, with the same 20 ms
orphan TTL. Tickets, retired IDs, metadata identities and trace lengths retain
their VXB bounds. Peak RSS remains unclaimed: joins, canonical assembled eyes,
previous publication, source vectors and Python metadata are additional.

Small blocks can have just one source fragment. Their parity duplicates the
whole source body, regardless of K. Thus nominal 1/K repair intuition can badly
understate this format's real wire cost. qualification.json records every record,
actual parity bytes and total offered bytes against the corresponding VXB
no-recovery stream. Trailing parity is counted even if complete stereo publishes
before that parity reaches the receiver. No loss probabilities are assumed.

Qualification
-------------

Run test_transport.py with unittest from this directory, then qualify.py with
the same --manifest, --export, --corpus and --build-receipt arguments documented
in ../rowblock_stream/README.txt and a new --out path. It reuses the unchanged
ASan/UBSan native stereo oracle and its bound compiler receipt; the VXP protocol
itself is tested in Python. Both format source files, shared lifecycle code,
qualification helpers, test source, all corpus inputs and the native receipt/
binary are hashed and rechecked after the run.

All 38 exported eyes and four row sizes are exercised with each K: 12 actual
capture pairs plus 14 explicitly duplicated-eye synthetic profiles. Clean,
shuffle, duplicate, one-manifest loss, one-DATA loss/corruption/truncation,
one-source-per-group loss and all-parity loss must publish exactly. Losing a
source and its parity must withhold. Two-source loss is exercised where a block
has at least two source fragments; inapplicable one-source-only cases are
explicitly marked, not counted as tests of that failure mode.

Logical record arrivals advance by 1 microsecond; this is a correctness input,
not a network/CPU latency measurement. Any benefit from actual block availability,
overlapped compression/transfer/reconstruction, physical loss recovery, pacing
or headset consumption remains untested. Same decoded Q8 bytes are preserved,
not a claim of lossless original input.
