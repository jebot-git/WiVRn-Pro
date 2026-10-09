Rolling feedback snapshot candidate
===================================

This default-off server pacer candidate changes the feedback bank supplied to
the existing recent-tail estimator. It retains a cumulative 5,000-slot live
ring and copies it under the existing compute mutex on each admitted stream-0
frame divisible by 100. The default alternating swap is unchanged. The finite
recent-frame window, support minimum, 99.5th percentile, margin, downward slew,
newest-frame watermark and reset epoch remain unchanged. No target or deadline
is moved directly by this change.

Motivation is the recent-tail ABBA at pacer_abba_audit/README.txt: selected-frame
ready residency remained substantial, and the two alternating banks can contain
different ages of feedback. That bracket had control drift and does not establish
a causal latency improvement. This candidate has offline correctness evidence
only; no rolling-mode native headset timing, latency pass or quality pass exists.

Feedback semantics and ownership
-------------------------------
The native first-display admission and clock conversion remain unchanged. Each
slot retains the maximum available valid feedback for its frame. A later peer-eye
report can enter the next snapshot; the snapshot does not prove complete paired
feedback. Reports rejected by the native eight-frame in-flight identity or by
the existing feedback admission do not enter the ring. Stale ring entries remain
subject to the existing recent-frame window and support rules.

Feedback and reset use mutex then compute_mutex. The worker releases compute_mutex
before taking mutex to apply a result. An epoch change rejects an obsolete copy.
The enabled stop predicate remains synchronized under compute_mutex before notify;
the worker joins before trace destruction. This does not change the owner's
pre-existing obligation to stop feedback callbacks before destroying the pacer.

Rolling schema-2 telemetry identifies the mode and 5,000-slot depth, records host
copy begin/end and worker apply times, and accounts for produced, consumed, empty,
coalesced and still-unconsumed snapshots. A reset contributes an empty batch but
need not wake the worker immediately. Coalesced/pending batches are disclosed;
they are not relabeled dropped raw feedback or complete acquisition coverage.
The sidecar covers consumed worker snapshots and contains no complete raw feedback
population. Zero dropped trace records, valid decisions and complete footer are
still required. Copy duration is host instrumentation, not an end-to-end saving;
the extra copy has a real cost that a live comparison must retain.

Controls and root-harness integration
------------------------------------
Native environment (research build only):
  WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES=900
  WIVRN_CODEC_TEST_PACER_BANK_MODE=rolling
  WIVRN_CODEC_TEST_PACER_TAIL_TRACE=<exclusive run>/host/pacer-tail.jsonl

Absent/empty/alternating bank preserves the old bank. Rolling with window zero
or any unknown mode rejects. Existing source/pacer/candidate diagnostics and
custom CPU NEON remain required by the pure harness helper.

Root owns native_compare.py. Its additive integration is:
  CLI --pacer-feedback-bank choices=(alternating,rolling), default=alternating
  provenance['pacer_feedback_bank'] = args.pacer_feedback_bank
  pacer_tail.validate(args, source)            # before ADB, returns four helpers
  env.update(pacer_tail.configure(args, out)) # after inherited test-env clearing
  pacer_tail.evaluate(out, args.pacer_window_frames,
                      args.pacer_feedback_bank)
The third evaluate argument explicitly joins the request, provenance and native
header. It defaults to alternating for historical callers. Missing historical
provenance bank also means alternating. Evaluation failure must fail acquisition;
none of the existing source/candidate/publication/pacer/latency gates is relaxed.
The helper does not own clearing inherited server environment variables.

Only pacer.cpp, pacer.h and codec_pacer_tail.h change in the native overlay.
codec_pacer_trace.h remains a reviewed dependency. This branch starts at the
95-file union 516ea2af4adc43119c43af6d572fe23043edd45b. The old integration.patch
and overlay-manifest.json are deliberately unchanged: root must regenerate its
current 97-file union after collection, verify parity and build a new server.
Do not apply this branch's stale integration.patch as the rolling implementation.
No compositor, foveation, decoder, target selection or client code changes here.

Offline evidence and reproduction
--------------------------------
results/qualification.json binds every receipt and the current test/source files.
The six subsidiary receipts preserve exact compiler/runner argv and source hashes.
They passed before publication:
  - Actual native on_feedback + worker, ASan/UBSan and TSan: each 33,636,808
    assertions, 67 snapshot triggers and 6,601 seeded frames. An independent
    cumulative ring oracle covers missing/ignored feedback, late peer reports,
    stale in-flight reports, reset and modulo-5,000 wrap.
  - Existing actual-worker ASan/UBSan and TSan regressions.
  - Pure helper under -ffast-math + ASan/UBSan: 4,141 checks, 2,000 randomized
    quantile/reference cases, mode admission and trace accounting.
  - Actual configured host pacer translation unit, codec-tests enabled and
    disabled, syntax-only with existing strict native compiler options.
  - 28 Python test methods: original/schema-2 analyzer and pacer harness tests.
  - Disposable semantic negative: replacing the rolling copy with the old swap
    makes the actual-feedback cumulative-ring oracle fail, without sanitizer error.

From repository root, use new output paths (receipts never overwrite):
  python3 prototypes/vr444/experiments/pacer_rolling_snapshot/check_worker.py \
    --sanitizer address,undefined --out /tmp/rolling-asan.json
  python3 prototypes/vr444/experiments/pacer_rolling_snapshot/check_worker.py \
    --sanitizer thread --out /tmp/rolling-tsan.json
  python3 prototypes/vr444/experiments/pacer_rolling_snapshot/negative_swap.py \
    --out /tmp/rolling-negative.json
  python3 prototypes/vr444/experiments/pacer_recent_tail/worker_check.py \
    --sanitizer address,undefined --out /tmp/legacy-asan.json
  python3 prototypes/vr444/experiments/pacer_recent_tail/worker_check.py \
    --sanitizer thread --out /tmp/legacy-tsan.json
  python3 -m unittest discover -s prototypes/vr444/experiments/pacer_recent_tail \
    -p test_analyze.py -v
  python3 -m unittest discover -s validation/wivrn-codecs/tests -p 'test_pacer*.py' -v
The aggregate receipt records the pure-helper build command. Native TU replay
requires a compatible configured host build: copy these four pacer sources into
a disposable directory, replace only the pacer.cpp input in its compile_commands
entry, remove -c/-o, append -fsyntax-only, and repeat without WIVRN_CODEC_TESTS.
The recorded configured native tree was read-only. External API stubs provide
runtime/time services in the worker tests; they are not headset execution and
their fixed clock cannot measure timing. No device, socket or graphics operation
was performed by these tests. Codec independently reviewed native lock ownership
and analyzer/helper accounting and found no remaining material blocker.

Bounded paired test proposal, not an executable authorization
-----------------------------------------------------------
After root reconciles/builds/pins the union and obtains renewed headset-worn
readiness, compare alternating/rolling/rolling/alternating at the same nonzero
900-frame window, same build, scene, RRB4 Q8 settings and 30-second duration.
Keeping the estimator enabled in both arms isolates bank choice. Capture all
existing source/attempt/candidate/pacer/publication gates plus tail sidecar; stop
on the first strict evidence failure. Retain period/focus changes and both control
drift and per-arm warm coherent full-stereo metrics. Include copy/apply spans,
support/fallback/slew, newest-feedback lag and coalesced/pending accounting.
All timing remains diagnostic until the existing independent latency gate passes;
this branch does not grant promotion or full-goal eligibility.
