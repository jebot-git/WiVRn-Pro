Exact stereo codec / owned UDP interoperability
===============================================

This isolated branch connects actual encoded content through the previously
separate VXF1, UDP batching/GSO/GRO and exact codec experiments. It is a correctness
qualification, not a network latency measurement, production transport, stereo
publisher, headset test or promotion. No external address/port is selectable.

Actual path
-----------
1. Read the frozen worker corpus: 12 actual captured stereo pairs plus 14 explicitly
   duplicated-eye synthetic profiles. Verify every original payload against its
   measured-corpus SHA-256. Reproduce the exact baseline VR444/WPC1 raw fallback
   policy, or read independently verified RRF-up / RRB32 / RRB128 exports. Encoded
   total pair length must equal the corresponding worker benchmark value.
2. Enforce the unchanged 256 KiB VXF encoded-pair cap. Oversized cases are explicitly
   ineligible; no truncation, quality reduction or larger resource profile occurs.
3. Packetize the actual two encoded eyes as VXF1. Send the resulting real framing
   and content through newly owned ephemeral 127.0.0.1 sockets using mmsg, GSO+mmsg
   or GSO+GRO. The native helper reconstructs and returns the actual received packet
   records only after the complete byte/count multiset passes its independent
   oracle. At most 32 already-available records are outstanding; no wait-to-fill.
4. Separately replay those received records into the actual VXF reference receiver.
   This replay uses deterministic 1 microsecond logical inter-record increments,
   a fresh session/epoch/ticket and a 100 ms logical deadline. It is NOT the actual
   socket arrival timing or proof of elapsed-time deadline admission. The native
   socket exchange itself retains its bounded 100 ms execution budget.
5. Require both reconstructed encoded eyes to equal the input bytes. Every successful
   pair then goes through a native ASan/UBSan decoder process and is compared byte
   for byte with each eye's independent expected RGBA export. Both eyes, dimensions
   and expected pixels must pass before any success response can escape.

The VR444 baseline retains the benchmark's optional WPC1 wrapper (only if its
compressed mode saves at least 64 bytes including framing); otherwise it retains
the original raw VR444 packet. It does not silently substitute uncompressed size
for the measured baseline. RRF/RRB exports preserve already-decoded opaque RGB.
Exactness of an existing Q8 reconstruction is not original-input losslessness.

Native helpers accept only bounded binary stdin records and produce stdout records.
relay: UPX1, u32 record count, repeated u32 length + packet (1..512, 1..1200 bytes).
It emits UPY1, count/send calls/receive calls/max GRO segments, then actual packets.
decode: CDP1, u32 codec selector (0 VR444/WPC1, 1 RRF1, 2 RRB1), then four u32-length
fields: encoded left/right and expected RGBA left/right. Encoded pair <=256 KiB,
expected output <=4 Mi pixels per eye. The encode-only baseline helper uses CEP1
with two bounded raw payloads and returns CEY1 plus two canonical encoded payloads.
Trailing, truncated, invalid-length or unknown-mode input rejects without stdout.

The only changes to inherited socket helpers add optional post-clock snapshots of
actual received records; default calls keep their prior behavior. The snapshot is
assigned only after the whole multiset comparison passes. No new timed comparison
is made with these modified helpers; old performance receipts describe their old
source hashes and remain historical evidence. CRC and parity are not authentication.

Fault and publication boundaries
-------------------------------
Four deterministic cases per eligible codec/socket arm:
  clean, no FEC;
  clean, XOR stripe width 8;
  first source body bit corrupted without repairing its packet CRC, with FEC;
  source shard 0 and its parity shard both omitted, with FEC.
Corruption is transmitted through the actual owned sockets. VXF must reject that
record and recover the original exact bytes with parity. The final case must never
return a complete pair. A separate negative test sends a codec-invalid right eye
inside otherwise valid transport CRCs; transport acceptance must not bypass codec
validation, and native stdout must remain empty. Shape and expected-eye mismatches
also fail without partial success. Previous production image retention and a real
external publisher/consumer are not implemented or claimed here.

Results
-------
All three socket arms supported on this host. The full frozen corpus produces:
  104 codec cases (26 stereo profiles x 4 codecs);
  12 explicitly over-cap synthetic codec cases;
  1,104 actual loopback exchanges (92 eligible cases x 3 arms x 4 fault cases);
  828 exact complete stereo decodes, each under ASan/UBSan;
  276 deliberately corrupted source shards recovered exactly;
  276 source-plus-parity omissions withheld as incomplete.
All 48 captured-pair codec cases fit; the cap rejections are synthetic only. GRO
coalescing was actually observed up to 32 segments; this does not establish NIC,
WiFi or headset support. The 228 read-only input/export files, all direct
codec/LZ4/socket implementation inputs and both native binaries are hashed
before/after the final qualification. Benchmark, measurement and native build
receipts are also bound and rechecked. The host build receipt records the exact
successful compiler argument lists, compiler versions, sanitizer flags, source
hashes and binary hashes; a --binaries directory without a matching receipt rejects
before sockets. This is reproducibility evidence, not authenticated attestation.

13 focused Python/native tests additionally cover mixed sizes across the 32-record
boundary, single-byte packets, no-FEC loss, independent 1x1 and 17x19 raw-format
goldens, real codec recovery in all supported socket arms, right-eye failure,
stereo shape/output mismatch, malformed native/Python framing and four unbound
build-receipt mutations. Success requires empty native stderr; expected rejection
requires ordinary exit1 without sanitizer diagnostics. The unchanged
reference VXF suite remains independently qualified. Socket regressions rerun:
9 batch roundtrips with forced partial syscalls/foreign-peer/truncation rejection;
232 offload roundtrips and 19 rejection checks. Both new helpers compile for
Android arm64 API29; no installation, launch or device access occurred.

Reproduce
---------
  bash prototypes/vr444/experiments/codec_udp_interop/build.sh
  python3 -m unittest discover -s prototypes/vr444/experiments/codec_udp_interop -v
  bash prototypes/vr444/experiments/codec_udp_interop/build-android.sh
  python3 prototypes/vr444/experiments/codec_udp_interop/qualify.py \
    --corpus /path/to/compact/corpus \
    --rrf-export /path/to/verified/rrf/export \
    --rrb-export /path/to/verified/rrb/export \
    --output /tmp/codec-udp-interop.json

The export tools and formats are inherited unchanged from rgb-rowfilter and
rgb-rowblocks. No capture pixels are committed here. results/qualification.json
contains every socket/fault outcome, exact output hashes, counters and provenance.
The adapter does not select external peers, change routes/sysctls, use ADB, install
anything or connect to the headset. No sockets are left open after each helper.

Missing evidence: authenticated negotiated protocol, real NIC/path behavior,
congestion/pacing/queue contracts, real-time deadline integration, target execution,
production consumer lifetime and the separately authorized complete headset gate.
