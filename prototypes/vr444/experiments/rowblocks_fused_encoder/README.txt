Fused bounded tile-band reconstruction and row-block encoding
============================================================

Offline server transcode candidate based on b8371b8. The source is existing
VR444 encoded data, because the frozen corpus has no original server framebuffer
pixels. Output preserves the existing decoded opaque RGB exactly, including prior
Q8 loss. This is not an original-framebuffer encoder or a production integration.
The published RRB1 format and client APIs remain unchanged.

transcode_pair preflights both immutable source frames, requires equal shapes and
axes <=4096 / at most4Mi pixels per eye, and indexes validated tile-row records.
Each job reconstructs one16/32/64/128-row band and immediately encodes canonical
RRF bytes. A persistent pool has1..4 workers, at most512 jobs and one synchronous
batch; each worker owns one reusable source-band/RGBA/compression scratch set.
Completed records are copied into disjoint block slots before scratch reuse.
One drained barrier precedes serial canonical assembly and CRC of both RRB eyes.
There is no frame queue, temporal reference or cross-frame dictionary.

Bands start on16-row tile boundaries. Private source headers retain width,
version and quantizer, changing only local height and tile count. Original tile
records, per-tile qmaps and coefficients are copied exactly. Independent VR444
tile reconstruction therefore preserves tile coordinates and edge cropping;
the direct decoder revalidates each reframed band and all coefficient bounds.
Every output must equal the existing serial RRB encoder byte-for-byte for all
three policies (plain/up/adaptive), including high-entropy raw fallback.

Inputs, workspace and pool require exclusive ownership throughout the call.
Alias preflight covers the derived object, base vectors, offsets, all four worker
scratch sets and their nested buffers. Alias failure preserves the prior lease;
subsequent exceptions clear both private candidates after every worker drains.
Returned spans expire on next call/clear/destruction. Retain an already published
pair in a separate workspace. Session/config/frame identity, deadlines and atomic
external publication remain the caller's responsibility. The RRB per-eye bound
is12,593,184 bytes; no transport limit is enlarged.

Qualification
-------------
ASan+UBSan and ThreadSanitizer each pass733 exact canonical stereo cases, including
300 repeated batches,512 jobs/two4Mi-pixel eyes,4096-wide odd shapes,v2 qmaps,
different eye quantizers, all block sizes/policies/worker counts, bad late-band
coefficients thrown inside a worker, unsupported8Mi-pixel input, malformed frames,
alias classes and an independently retained prior publication. Rejection counts
are3459 and3465 respectively: active scratch-buffer alias checks vary with which
workers acquire jobs. Both runs complete without diagnostics. The original pool's
generation/concurrent-call/failure tests are inherited unchanged. An independent
compositor-team read-only review found no bounds, lease or concurrency blocker.
Android arm64 API29 binaries cross-compile only; no device execution occurred.

Benchmark
---------
26 profiles contain12 actual capture pairs and14 explicitly duplicated-eye
synthetic profiles. Twelve arms rotate after3 warmups: VR444 plus optional WPC1;
global RRF-up serial; and RRB32/RRB128 original reference, staged workers4, fused
serial, fused workers2 and fused workers4. Every RRB client uses the same four
workers. Existing reconstruction/validation, scratch copies, compression, barrier,
assembly and CRC are included. Independent canonical-wire and decoded-pixel checks
run outside the clock for every invocation. Pool construction and warmup allocation
are excluded. Server and client run sequentially on one host, not actual endpoints.
Wall intervals include two process CPU clock reads; process CPU aggregates workers.
Retained capacity counts algorithm-owned vectors, not allocator overhead, thread
stacks, inputs, oracles or peak resident memory. It is not a peak-memory claim.

RESULTS.txt and results/ preserve all matched samples, source/corpus/binary hashes
and limitations. Four captured pairs regress at p95 despite every pair improving
at p50; this candidate is not promoted to a strict sub15 or headset result.

Reproduce
---------
  RRB_FUSED_TSAN=1 bash prototypes/vr444/experiments/rowblocks_fused_encoder/build.sh
  /tmp/wivrn-rowblocks-fused-encoder-build/test
  /tmp/wivrn-rowblocks-fused-encoder-build/test-tsan
  bash prototypes/vr444/experiments/rowblocks_fused_encoder/build-android.sh
  # Only after a coordinated host measurement window:
  python3 prototypes/vr444/experiments/rowblocks_fused_encoder/measure.py \
    /path/to/compact/corpus /tmp/rowfused

No headset, network, GPU, ADB, installation or production configuration is used.
