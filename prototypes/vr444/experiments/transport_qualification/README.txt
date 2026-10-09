Transport and exact-compression team handoff, 2026-10-09
======================================================

22 independent research approaches are pinned in catalog.json. Every approach
was committed and pushed under research/sub15-20261009/<name>. This catalog is
a separate handoff branch; it does not merge the approaches into production or
authorize headset tests. Headset testing remains suspended.

The most promising host CPU result is rowblocks-band-views: checked immutable
VR444 tile-record bands feed vector RGB reconstruction and four persistent
workers, with canonical RRB output. Its final matched host batch had lower
captured server-plus-client p95 than VR444 in all 12 pairs. That is one desktop
CPU batch, not the headset receipt-to-post-xrEndFrame metric. The GPU team's
separate RRB image paths have their own device-specific evidence; results from
different batches/devices cannot be added into an asserted end-to-end speedup.

The transport alternatives remain bounded prototypes. UDP batching/offload
helps on owned localhost; no physical wireless throughput/latency claim follows.
VXF/VXI/VXB/VXP use CRC and optional XOR, not authenticated transport. They need
production identity, capability, congestion, ownership and lifetime integration
before any device experiment. No production interface, route, listener or codec
selection was changed by this team.

Pinned approaches and interpretation
-----------------------------------

Name                          Commit    Interpretation
rgb-palette                   a686dfe3  Exact decoded RGB tiles; smaller/faster client in some cases, server cost prevents full-CPU promotion.
datagram-xor                  726d7c39  Receiver-issued tickets and bounded per-pair XOR recovery; logical loss model only.
udp-batch                     3075ff7c  Owned localhost sendmmsg/recvmmsg; fewer calls and lower host burst time.
transport-queue               512f3b89  Latest-pending bounded queue model; freshness and completion/cadence remain distinct.
static-dictionary             5eb6ac14  Immutable per-frame dictionary reset; no captured wire win and added server cost.
udp-offload                   74e678a1  UDP_SEGMENT/GRO strict framing and owned localhost qualification; device support untested.
rgb-rowfilter                 2773a29a  Exact RGB row-up residual+LZ4/raw fallback; Q1 byte savings, scalar CPU tradeoff.
rgb-rowfilter-simd             895e9eff  SSSE3/SSE2/NEON exact pack/expand/predict; useful isolated client stage.
xor-interleave                db48e2ac  Bounded distinct interleaved XOR mapping; burst and periodic loss tradeoffs.
rgb-rowblocks                 52b355f7  Independent 16/32/64/128-row RRF blocks, canonical RRB stereo.
rowblocks-workers             260a1a22  Bounded persistent client workers; faster client, original serial server cost remains.
rgb-channelplanes             afb5b592  Exact planar/green-delta alternatives; tiny byte savings and worse CPU cost.
codec-transport-budget        87236b33  Matched component and real framing arithmetic; declared service, not network measurements.
codec-udp-interop             f1fe2e43  Actual codec bytes through owned loopback and bounded assembly; independent stereo pixel checks.
rowblocks-encoder-workers     b8371b8b  Parallel server blocks after full-eye decode barrier; negative full CPU comparison.
rowblocks-fused-encoder       355b3b13  One bounded band decode/encode barrier; positive medians with explicit tail regressions.
latency-wire-frontier         4ca8993c  Separate full-horizon/first-receipt wire-service frontiers; no cross-domain promotion.
rowblocks-rgb-encoder         2066d453  Removes RGBA intermediate using scalar RGB stores; mixed/negative against matched control.
rowblocks-rgb-vector          a0ee6743  Vector color/store path; stronger matched CPU results with two VR444 tail regressions.
rowblocks-band-views          a55dff16  Removes private input-band copy/reframing; strongest final captured CPU tail result in one batch.
rowblock-stream               7f097f3d  VXB independent block emission, exact complete-stereo assembly; overlap remains unmeasured.
rowblock-stream-fec           8e5d1d50  VXP per-block XOR repair; correct, but captured parity overhead is a substantial negative.

Branch URL form:
  https://github.com/jebot-git/WiVRn-Pro/tree/research/sub15-20261009/<name>
catalog.json contains full immutable commit IDs, directory file inventories,
artifact hashes and every recognized declared repository binding difference.

Historical integrity is separate from fresh execution
------------------------------------------------------

catalog.py inventories all 142 notes/JSON artifacts in the 22 pinned approach
directories, including intentionally retained earlier receipts. It verifies
artifact bytes and 521 matching repository bindings. Sixteen declared snapshot
differences remain explicit and prevent a blanket historical-source pass:

* rgb-palette: two older timing receipts precede later inspect/golden/export API
  additions; five paths differ in each. Old timings are not measurements of the
  final expanded API implementation.
* rgb-rowfilter-simd: four inherited scalar source hashes differ from the SIMD
  branch; the separate simd-results receipt binds the actual SIMD experiment.
* rgb-rowblocks: the later README and alias-failure contract comment differ from
  the original measurement snapshot. Documentation intent does not rewrite hashes.

No missing source history is concealed by these explanations. The catalog checks
the exact recorded and pinned digests and preserves an incomplete history status.
It does not replay historical benchmarks, resolve every original compiler/include
environment, verify remote refs, or claim external corpus/cache/binary presence.
Only recognized explicit digest maps are audited; complete dependency coverage
is not inferred from an artifact's existence. External/contextual hashes remain
unverified in this Git-only stage.

run.py extracts each of seven Python suites from its own pinned Git tree into a
temporary directory. It rejects links and path traversal, checks every extracted
file against its Git blob BEFORE execution, runs unittest with a bounded timeout,
requires a nonzero test count and plain OK/no skips, and rechecks source bytes.
It does not combine mutually exclusive branches into an artificial source tree.

Optional --native-band-directory executes the historical band-view ASan/UBSan
and TSan binaries only after their hashes and pinned dependency bindings match
the qualification receipt, and checks those binary hashes again afterward.
This is fresh execution of byte-bound binaries, not a rebuild or an assertion
that the original compiler/system environment was reproduced. That suite covers
733 canonical stereo cases, 576 independent RGB oracle cases, 2000 differential
corruption cases, repeated reuse, odd dimensions/Q maps, and 512-job/4-MP bounds.

Reproduce the handoff checks
---------------------------

From this directory:
  python3 -m unittest -v test_catalog.py
  python3 catalog.py --out /tmp/transport-history-new.json
  python3 run.py --out /tmp/transport-python-new.json
Optional bound native execution on the original host:
  python3 run.py --native-band-directory /tmp/wivrn-rowblocks-band-views-build \
    --out /tmp/transport-python-native-new.json

All output paths must be new. The runner writes outside its source directory;
it checks its own source/catalog snapshot before/after. Loopback interop and
historical performance remain separate receipts, not fresh runner stages.

Evidence limits and next integration work
----------------------------------------

The common exported corpus has 24 captured eyes (12 matched stereo pairs) plus
14 synthetic eyes, explicitly duplicated across eyes for paired synthetic tests.
Exact Q8 reconstruction preserves the existing decoded Q8 bytes, not original
input losslessness. Desktop tests lacked fixed affinity/frequency/thermal state;
31 repeated samples do not establish broad-device or long-tail confidence.

A future integrated suite must preserve complete stereo admission, immutable
source/staging leases, explicit ticket origin, corruption/malformed rejection,
bounded queues, final image/color correctness and the original latency trace
contract. It must bind the chosen server codec, transport and GPU client in one
build and one matched capture before making an end-to-end claim. CPU, GPU, wire
and compositor metrics have different boundaries and are not interchangeable.
No headset launch is performed or authorized by this handoff.
