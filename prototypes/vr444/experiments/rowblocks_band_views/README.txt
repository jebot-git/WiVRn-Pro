Immutable tile-band views without private source copies
=======================================================

Offline server candidate based on vector encoder a0ee674. It retains the same
exact RGB reconstruction, per-worker RGB/compression staging and canonical RRB
output, while removing each job's private source-band copy and reframed24-byte
header. Inputs remain immutable for the entire synchronous stereo call.

The outer transcode_pair preflights both original VR444 frames, requires equal
shapes within axes4096/4Mi pixels, and indexes every complete source tile row.
Each16/32/64/128-row job receives a bounded view of those original record bytes.
The view starts at a16-row boundary, retains original width/quantizer/version and
uses the exact local height/tile count. No view escapes the drained worker batch.
The full source frame identity and band's membership are established by this
outer validator/index, not inferred from arbitrary raw record bytes.

The internal headerless decoder independently treats its explicit shape as
untrusted: legal version/quantizer/axes/pixel/tile-count bounds and every local
length/qmap record are checked before any pixel write, with exact end consumption.
It then retains the original coefficient, sparse-tail, padding, saturation and
cropped-store checks. Valid metadata describes a headerless band's interpretation;
it does not independently authenticate membership in an original frame. Only the
outer transcode API supplies that original-frame relationship. This is not a new
network format or a public production decoder negotiation.

The immutable source views may be shared read-only among workers. RGB/predictor/
compression scratch stays private to each worker; completed wire blocks are copied
into disjoint slots before reuse. One barrier drains at most512 jobs before both
canonical eyes are assembled. Every later failure clears both candidate outputs
after workers drain; input alias rejection happens before mutation and preserves
the old lease. Returned spans expire on next call/clear/destruction. Independent
session/frame/deadline checks and external stereo publication remain the caller's
responsibility. No transport maximum or quality contract changes.

ASan+UBSan and ThreadSanitizer each pass733 canonical stereo cases,576 scalar/
vector RGB oracles,2000 differential corruptions and6256 rejection checks. The
headerless API also rejects every truncation of an independent v2 band, invalid
versions/quantizers/shapes/tile counts, bad record lengths/qmaps and trailing data
without modifying its output. Existing overlap, late coefficient failure, odd
tails, every block size/policy,512 jobs/two4Mi-pixel eyes,300 reuse calls and
independent prior-publication lease checks remain. Android API29 is compilation
only. Full26-profile x12-arm x5 smoke passes before measured timing.

The twelve-arm comparison keeps vector RGB with source-copy workers4 and original
fused RGBA workers4 as controls, plus this candidate serial/workers2/workers4 for
32/128-row blocks. VR444+optional WPC1 and global RRF-up remain baselines. All RRB
clients use the same four-worker decoder. Exact canonical-wire/pixel checks run
outside the clocks for every invocation. The source corpus contains prior VR444
bytes, not original framebuffers; exact Q8 means preserving prior decoded Q8 bytes.

Reproduce
---------
  RRB_BAND_VIEW_TSAN=1 bash prototypes/vr444/experiments/rowblocks_band_views/build.sh
  /tmp/wivrn-rowblocks-band-views-build/test
  /tmp/wivrn-rowblocks-band-views-build/test-tsan
  bash prototypes/vr444/experiments/rowblocks_band_views/build-android.sh
  # Only after a coordinated CPU measurement window:
  python3 prototypes/vr444/experiments/rowblocks_band_views/measure.py \
    /path/to/compact/corpus /tmp/bandviews

No socket, GPU, headset, installation or production configuration operation occurs.
Headset tests remain suspended; stage measurements cannot promote a sub15 result.
