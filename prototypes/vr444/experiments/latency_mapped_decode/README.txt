0.14 alternating mapped-output decode experiment

Self-contained sources copied from the verified 0.13 prototype: direct.cpp/.hpp,
codec.cpp/.hpp and upload scaffolding. Original 0.13 sources/builds are unchanged.
Only the output destination differs between the two measured variants:
  cached_copy: NEON -> reusable cached vector -> memcpy to coherent staging
  mapped_direct: NEON -> the same coherent mapped staging buffer directly
Both then use identical optimal R8G8B8A8_SRGB image upload, fence wait and a separate
exact image-byte readback. No wire grammar, inverse, quantization or shader changes.

Build/run
  bash prototypes/vr 444/experiments/latency_mapped_decode/build.sh [OUTPUT_DIR]
  OUTPUT_DIR/mapped-probe-host CORPUS 7
  OUTPUT_DIR/mapped-probe-android CORPUS 21
No shader directory is required. Parent owns Android transfer/execution. New corpus
at validation/results/latency-research-0.14.0/decode/corpus has 38 immutable captured
payload copies with source hashes. The binary appends 84 generated cases: partial
tiles, q 1/8/17/1024, constant/noise/checker patterns and all 48 three-word nonconstant
AC positions. A software Vulkan device is rejected. Device 0 graphics queue is used.

Fairness and oracle
For each case and repetition, first arm=(rep+case_index)%2, so order alternates;
three warmups precede measured samples. The full 122 case suite counterbalances
which arm goes first globally even with an odd repetition count. Each arm poisons
the image alpha to 0, cached output, mapped staging and separate readback memory;
all reference output alpha is 255. This preparation is outside timing. No host
read of mapped decoded output occurs until its timed upload is complete. Then
both decoded bytes and a separate actual image readback must equal the independent
reference CPU codec output. No partial output is uploaded on exception. Mapped
pointer alignment, exact output span and source/output nonoverlap are checked.

Measurements
cpu_decode_ms includes the direct decoder's full preflight/parser/inverse/stores;
staging_copy_ms is memcpy for cached_copy and exactly 0 for mapped_direct.
upload_submit_fence_ms excludes command recording but includes submit and wait.
decode_through_upload_ms includes decode/copy/recording/submit/fence. Separate
readback timers report validation overhead outside the measured upload pipeline.
Chronological paired_iterations preserve per-repetition association and arm order;
per-arm sorted samples and quantiles are also emitted. All timing uses host wall
clock, not isolated GPU transfer timestamps. Summarize/audit:
  python 3 prototypes/vr 444/experiments/latency_mapped_decode/summarize.py RESULT.json
The script verifies order, exactness, finite samples, sample/quantile consistency,
software-device rejection and zero memcpy time for mapped_direct. It reports both
mean case quantiles and paired mapped-minus-cached differences.

Memory semantics and scope
The same first-compatible HOST_VISIBLE|HOST_COHERENT memory allocation serves both
arms. Its exact flags/type and all advertised memory types/heaps are emitted.
HOST_COHERENT does not imply HOST_CACHED. Recorded 0.13 Quest staging flags 15 did
include HOST_CACHED; the new run verifies its own allocation. No alternate memory
type is silently chosen to favor one arm. The live VMA allocation may differ.
This is a private standalone graphics queue with no XR, LZ4, network, stereo
publication, sRGB sampling or display. Image TRANSFER_SRC usage is added solely
for the byte oracle. Poison/readback interventions change cache/queue state even
though excluded from upload timing. Shape and output allocation are reused; live
code adds another preflight and resize. Do not claim live/optical improvement or
promote the mapped path without worn controlled validation and actual memory-type
telemetry. Dense real-scene and long thermal behavior remain separate coverage.
