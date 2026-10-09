Bounded persistent workers for independent RGB row blocks (2026-10-09)

Scope
Offline C++ stereo decoder experiment on the unchanged RRB1 format from
../rgb_rowblocks. No native WiVRn scheduling or transport integration, headset
operation, thread priority/affinity changes, network listener or GPU work.
Two complete frames must retain the same decoded opaque RGB as the source VR444
pixels. Existing Q8 quantization is preserved, not reversed or called lossless
relative to original imagery. Every measured output is exact-checked.

Pool and bounded work
Pool owns1..4 persistent std::threads. One synchronous run holds at most512 jobs,
with no pending-frame queue. An atomic next-job counter distributes independent
blocks. A condition variable starts each generation; a mutex-protected completion
count ensures every worker is finished before run returns. Jobs never outlive the
stack context or input lease. A second concurrent run rejects before replacing
callback/context/count. Worker exceptions are captured, further scheduling stops
opportunistically, already active jobs finish, and the calling thread rethrows
only after the complete barrier. A later run can recover. Constructor failure
stops/joins already created threads. Pool destruction requires no concurrent use.
Workers sleep between batches; no busy spin, realtime policy or deadline extension.

Stereo API and publication contract
decode_pair(left_wire,right_wire,workspace,pool_or_null) first runs the existing
complete RRB1 preflight on both eyes: bounded dimensions/canonical table, every
RRF header/CRC and strict LZ4 grammar. Equal outer width/height are required; each
eye can use a different allowed block size. Only then are bounded private RGB and
RGBA staging ranges resized. Each job decompresses, restores its own row domain
and expands to a disjoint RGBA interval; it cannot read another block's prior row.
The null-pool path calls exactly the same jobs serially as the matched control.

Every successful job overwrites all its admitted RGB/RGBA bytes. Successful reuse
retains vector sizes to avoid redundant zero-fills. A malformed nonaliasing input,
dimension mismatch, allocation/decode/worker failure clears both staging outputs
and indices. Alias preconditions cover RGB, RGBA and index-vector allocations and
reject before changing state. No borrowed Stereo
spans are returned until every job completes successfully. There is no caller-
owned output that could be partially overwritten on failure.

Returned spans last only until the next decode/clear/destruction of that workspace.
A retained published frame MUST keep a separate owned workspace/lease from a new
candidate. This codec does not itself atomically publish two external images or
validate frame/eye/session/config identity. Integration must check those, recheck
its deadline after complete decode, and publish the complete stereo pair together.
The pool does not preempt an in-flight LZ4 block or promise a real-time deadline;
OS scheduling remains outside these bounds. Partial block output is never eligible.

At most512 jobs cover two eyes, each at most4,194,304pixels, with at most256 blocks
per eye. Private payload staging is at most24MiB RGB+32MiB RGBA for the pair, plus
bounded indices and caller-owned immutable wire. Previous published leases add
their own explicitly owned capacity. Vector capacity/allocator/thread stack costs
are not included in this payload accounting; no total process-memory cap is claimed.

Correctness and runtime qualification
Both ASan/UBSan and ThreadSanitizer executions pass217 stereo roundtrips and4,273
rejections. Coverage includes serial/1/2/4workers, odd/partial rows, distinct left
and right content, all modes via up/adaptive encoding, maximum two4MP images and
512jobs,4,000 per-eye corruptions, every selected-frame truncation, dimension and
alias rejection, injected worker exceptions with subsequent reuse, explicit active
batch rejection, and1,000 generations with exact-once job counters. A separate
published workspace remains byte-identical through failed new candidates. Four
independent RRB1 goldens pass serial and all three worker counts. No sanitizer
finding was emitted; these finite tests do not prove every possible schedule.
Android ARM64 API29 benchmark/test cross-compile only; no headset run occurred.

Benchmark definition
26 stereo profiles comprise12 actual captured left/right pairs and14 explicitly
duplicated-eye synthetic fixtures. Capture filenames must end-0/-1 and both must
exist; unknown or incomplete pairs reject. Source width/height/Q must match.
Eight rotating arms: VR444plainLZ4 serial; whole RRFup serial; RRB32 and RRB128 each
serial/2workers/4workers. RRB wire bytes are canonical and identical across worker
arms. Encoding stays serial in every profile. Server timing includes reconstruction
of both existing VR444 images plus both wrappers, client wall includes both-eye
preflight, private staging resize, dispatch/barrier, reconstruction and expansion.
Client CLOCK_PROCESS_CPUTIME_ID counts aggregate CPU consumed by all threads; it
is not elapsed time or energy. The two clock reads are included in client wall
for every arm. Exact comparisons and source-hash verification are outside timing.
Pool creation plus an initial ready barrier is reported separately; steady-state
has3 warmups+31 samples per arm. No queueing, transfer, upload, compositor/runtime
wait, optical timing or headset thermal behavior is inferred.

Final matched host result
The final batch ran26.98seconds after both other research teams explicitly
confirmed idle. Source hashes before/after match. Host is a12th-gen i7-12700 with
uncontrolled affinity/frequency/temperature and ordinary desktop/services. These
are means of per-stereo-profile medians, not pooled percentiles or headset timings.

Captured Q1, four actual pairs (wire column sums all four pairs)
profile           wireB     serverms  clientwallms  clientCPUms  combinedms
VR444serial        98,239    .137895       .547257      .540230     .689333
RRFupserial        55,655    .863008       .526917      .520423    1.387494
RRB32serial        66,332    .677140       .574843      .566996    1.264290
RRB32workers2      66,332    .672849       .457907      .704099    1.131175
RRB32workers4      66,332    .678939       .321041      .844598    1.022212
RRB128serial       58,298    .699050       .547597      .541077    1.250532
RRB128workers2     58,298    .682652       .460907      .680361    1.151409
RRB128workers4     58,298    .693833       .340426      .816341    1.045090

RRB32workers4 improves host Q1 client wall by.226216ms versus VR444, but consumes
.304368ms more client process CPU and adds server work. Combined server+client
wall remains1.022212ms versus.689333ms. Q1 wire saves32.48% versus VR444; RRB128
retains more compression but has slightly higher client wall than RRB32 here.
Q8 captured groups show the same resource tradeoff: RRB32workers4 client
.470635/.433112ms versus VR444 .899215/.749615ms, but combined1.714915/1.482703ms
versus1.097924/.923704ms, and wire grows34.26%/43.29%. More CPU may compete with
compositor/runtime work on a headset; that interaction and ARM runtime behavior
remain untested. No end-to-end improvement is established. Preserve as an explicit
client-side option, not a promoted global solution.

Pool2 construction+ready barrier was.062410ms; Pool4 .106770ms. These are single
setup observations, not comparative distributions. All per-case31-sample values,
including process CPU, small duplicated-eye fixtures and mode-independent wire
equality are retained in results/. Four-worker CPU cost is not hidden behind
lower wall time. Encoding is still serial; no server parallel speedup is claimed.

Independent compositor-team review found no generation/barrier or disjoint-write
blocker. It identified missing index-vector coverage in the input alias guard;
both index allocations are now checked, with targeted lease-preservation cases
passing under both sanitizer suites. Separate immutable publication leases remain
a caller requirement, not something these measurements emulate.

Reproduce from repository root
  RRB_WORKERS_TSAN=1 bash prototypes/vr444/experiments/rowblocks_workers/build.sh
  /tmp/wivrn-rowblocks-workers-build/test
  /tmp/wivrn-rowblocks-workers-build/test-tsan
  python3 prototypes/vr444/experiments/rgb_rowblocks/golden.py /tmp/rrb-worker-goldens --decoder /tmp/wivrn-rowblocks-workers-build/test
  bash prototypes/vr444/experiments/rowblocks_workers/build-android.sh
  /tmp/wivrn-rowblocks-workers-build/bench validation/results/codec-viability-0.8.0/compact/corpus 31 > /tmp/rrb-worker-bench.json
  python3 prototypes/vr444/experiments/rowblocks_workers/summarize.py /tmp/rrb-worker-bench.json /tmp/rrb-worker-summary.json

LZ4 independent-block background and wire contract remain documented in
../rgb_rowblocks/README.txt. Captured content is not committed; exact source hashes,
all per-profile samples and small correctness receipts are retained.
