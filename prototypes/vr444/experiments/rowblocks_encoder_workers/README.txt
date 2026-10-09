Bounded parallel canonical row-block encoding
=============================================

Experimental offline server transcode stage; unchanged RRB1 wire/API, exact
already-decoded opaque RGB, no network/GPU/device operation or latency promotion.
This branch inherits the published decoder worker pool at 260a1a22. It uses the
same synchronous persistent Pool (1..4 workers, maximum512 jobs, one active batch,
no frame queue). The pool's established exception path drains every worker before
returning a failure. Existing serial formats and implementations are unchanged.

The new transcode_pair API accepts two immutable original VR444 payloads. It fully
preflights both frames, requires matching dimensions bounded by axes4096/4Mi pixels,
and prepares private RGBA and per-block workspaces. Stage1 runs two independent
eye decode jobs; its barrier precedes stage2, at most512 independent RRF encoding
jobs over16/32/64/128-row blocks. Stage3 serially assembles both canonical RRB frames
with the original header/table/body CRCs. Both encoded eyes remain private until
the function returns. The bytes must match the existing serial rowblocks::encode
exactly for plain/up/adaptive filter policy, including raw high-entropy fallback.
No temporal dictionary or cross-frame dependency is introduced.

Every block owns its predictor, compression scratch and wire buffers; only the
completed RGBA source arrays are shared read-only. Metadata/vector storage is
stable during each pool batch. The first-eye/block mapping uses equal validated
stereo dimensions and block counts. All lengths are checked before outer assembly,
whose maximum size remains12,593,184 bytes per eye. This codec-stage maximum is
independent of the separate VXF256KiB pair limit; no transport cap is enlarged.

Caller contract: inputs, workspace and pool remain exclusively owned for the call.
Input spans must not overlap the workspace object, RGBA/wire/vector storage or any
nested predictor/compression buffer. Alias failure occurs before mutation, retaining
the prior lease. Every subsequent exception clears both staged candidates after
workers drain. Returned encoded spans last only until next call/clear/destruction;
retain a previously published pair in a different workspace. Session/config/frame
identity, deadlines, transfer and external atomic publication remain the caller's
separate responsibility; they are not implemented by this offline encoder.

Exactness scope
---------------
The source is existing VR444 encoded data because the frozen corpus does not
contain original server framebuffer pixels. Transcoding preserves those decoded
bytes exactly, including any prior Q8 loss. It is not an assertion that Q8 became
lossless or a benchmark of a live original-framebuffer encoder. Direct VR444
reconstruction, validation, both worker barriers, canonical assembly and CRC are
included in the transcode wall interval. A future already-RGBA input path would
have different costs and requires its own explicit comparison.

Qualification
-------------
ASan+UBSan and ThreadSanitizer both pass725 exact canonical stereo cases and3445
rejections: independent eye content/quantizers, v2 tile quantizer maps, odd sizes,
all four block sizes, all three policies, serial/1/2/4 workers, malformed framing,
valid-framing invalid coefficients thrown inside a worker, all workspace alias
classes, retained independent prior publication,300 repeated two-barrier calls,
maximum512 block jobs and two4Mi-pixel eyes. The malformed-coefficient test repairs
no transport CRC because this API intentionally consumes raw codec frames after
separate transport admission. Pool-level generation/failure/concurrent-call tests
are inherited unchanged from the original decoder worker branch.

Independent compositor-team review found no bounds, alias, lease or concurrency
blocker. Android arm64 API29 test/benchmark binaries cross-compile only; they have
not been installed or executed on a device. A full26-profile5-repetition smoke run
passes canonical encoded-byte and both-eye decoded-pixel oracles before measurement.

Matched benchmark
-----------------
26 inputs:12 actual stereo captures and14 explicitly duplicated-eye synthetic
profiles.10 arms rotate per repetition after3 warmups:
  original VR444 plus the established optional WPC1 compression wrapper;
  exact global RRF-up serial transcode;
  RRB32 original serial reference / new staged serial /2 workers /4 workers;
  RRB128 original serial reference / new staged serial /2 workers /4 workers.

Reference and staged RRB arms use the same direct-decoder validation semantics.
All RRB client arms use the same four-worker decoder, making differences among
encoder arms independent of a client worker-count choice. VR444 and global RRF
clients remain serial. Server and client stages execute sequentially on the same
host, not real separated endpoints. Per-stage wall time includes two process CPU
clock reads; the CPU metric aggregates all worker threads. Matched complete samples
are retained, and every output is compared with independent canonical serial wire
and reference decoded eye pixels outside the measured interval. Pool startup and
initial allocation warmup are outside the steady-state samples.

Single-host observations do not establish target CPU, scheduler, NIC or compositor
behavior. No affinity, frequency or thermal controls are applied. The final runner
requires a fresh quiet window from both other teams and records source, native/
Android binary and corpus hashes before and after. Group summaries are arithmetic
means of fixed pair p50s, not pooled/live latency percentiles. Detailed measured
results and limitations will be recorded separately in RESULTS.txt and results/.

Reproduce
---------
  RRB_ENCODER_TSAN=1 bash prototypes/vr444/experiments/rowblocks_encoder_workers/build.sh
  /tmp/wivrn-rowblocks-encoder-workers-build/test
  /tmp/wivrn-rowblocks-encoder-workers-build/test-tsan
  bash prototypes/vr444/experiments/rowblocks_encoder_workers/build-android.sh
  # Only after a coordinated host CPU measurement window:
  python3 prototypes/vr444/experiments/rowblocks_encoder_workers/measure.py \
    /path/to/compact/corpus /tmp/rowencoder

No headset, ADB, installation, production configuration or external listener is
used. Commit/push records this isolated approach without promoting it to runtime.
