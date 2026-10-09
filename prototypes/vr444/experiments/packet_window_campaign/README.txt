VWG1 finite adversarial campaign, 2026-10-09
============================================

This supplementary qualification exercises the unchanged packet-window-fec
protocol pinned at 788ba8b4c1bfb931c17d7a2ae9ad237c5ed48afc. It does not introduce
another wire format or a production integration. No sockets, headset operations
or comparative timing occur. The 23-approach transport catalog remains separately
pinned at 50086f07ef9db98299620db7dd696d97f951a1ba.

Publication-order controls enumerate 432 cases: K=4/8/16, either redundant
manifest omitted, valid/overlong-zero/invalid-recovered-header parity, and all 24
arrival permutations of the remaining three necessary records plus parity.
Valid parity must publish exactly; malformed parity known before the final
necessary source must withhold. A malformed redundant record arriving only after
complete valid publication cannot retroactively invalidate that publication.
Exactly 216 cases publish and 216 withhold under this declared order contract.

The main campaign has 4800 deterministic two-frame trials covering the complete
20 mutation types x 3 K values x 4 shapes x 20 seeded schedules. Every joint cell
and count is asserted. Shapes are 1x1, 17x33, 31x65 and 64x17 with odd row tails;
these are small raw-RRF fixtures, not the captured or maximum-size native corpus.
Each trial mutates one record, then applies declared seeded 8% record removal,
10% duplication and full shuffling. These synthetic generator parameters and
completion counts are not measurements or estimates of wireless behavior.

Semantic mutations repair the outer CRC. Inner DATA field/body mutations also
repair the inner packet header CRC, retaining the independent block checksum.
Cases cover malformed outer identity/version/kind/K/slot/group/count/handle,
inner eye/reserved/frame/length/shard/CRC/body fields and parity body/length.
Raw bit changes, truncation and extension separately exercise framing/CRC gates.
CRC and XOR are not authentication: deliberately self-consistent replacement
pixels with all checksums recomputed are outside this exact-original-byte oracle.

The oracle independently saves the complete original canonical eye bytes for
each frame. Every publication must match both eyes, its frame and warp, and
strictly advance the published frame ID. Per-receive checks enforce candidate,
ticket, group, window and retained-payload bounds. Every trial then expires the
ticket and replays old records, reconfigures the receiver, and replays them again;
neither stale path may publish. Twenty-four separate clean/single-source-loss
controls require both consecutive frames to publish, preventing an always-drop
implementation from passing. The highwater field samples retained payload after
each receive; it excludes transient allocations, Python metadata and published
pairs and is neither peak RSS nor a new allocation bound.

The runner verifies all three protocol files against immutable Git blobs before
execution and rechecks the complete local campaign/protocol source hashes after.
Finite seeded coverage is not an exhaustive grammar proof. This Python byte-flow
campaign neither executes a native pixel oracle nor rebuilds sanitizer binaries;
those remain separate receipts in the pinned codec/protocol approaches.

Reproduce from this directory:
  python3 campaign.py --out /tmp/vwg-campaign-new.json --trials 4800
Use a new output path. --trials must be a multiple of 240 in [2400,19200].

Independent read-only compositor and GPU reviews found no publication-oracle
blocker. The compositor review identified the initially correlated joint input
schedule, which was corrected before the final run; the GPU review clarified
post-receive retained-payload reporting. No protocol source changed in this branch.
