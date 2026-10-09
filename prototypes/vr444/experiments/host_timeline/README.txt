Recorded selected-frame host callback timeline
=============================================

This diagnostic analyzes immutable run 05 after strict candidate/source trace
validation. It does not modify native behavior, join clock domains, change any
latency gate, or rewrite the original failed acquisition. The failed candidate
summary remains original; the previously documented skipped-attempt repair is
used only for explicit reanalysis. All 300 original manifest files and the
exact source-helper hashes are checked before and after analysis.

Recorded scope and results
--------------------------
The first two seconds of actual client submissions define warm membership.
Only the first submission of each encoded frame is counted; source identities
seen before warm-up are retained. All 1,900 warm first encoded submissions join
to exactly one direct projection and one latched application generation.

Host-local interval                          p50 ms      p95 ms      p99 ms
wake -> begin                              0.025107    0.122235    0.169254
begin -> delivered                         1.241297    1.912237    2.492744
delivered -> GPU done                       0.222587    0.459735    0.798910
GPU done -> latch                          0.019045    0.102911   10.036994
latch -> compositor input acceptance       0.009169    0.026305    0.039709
whole wake -> input acceptance             1.580050    2.361252   12.882047

These are nearest-rank quantiles. They cannot be summed across rows. Each
individual sample's five intervals does telescope exactly to its own whole
interval. The actual p95-ranked whole sample is encoded frame 1327, app 1325:
  0.190731 + 2.109079 + 0.044495 + 0.009892 + 0.007055 = 2.361252 ms.

There are two intentionally distinct source-history classifications:
* 1,888 source sets first seen in actual selected history and 12 already seen.
  This selected history includes all pre-warm submissions, but does not infer
  that an application generation's earlier host frame was selected.
* 1,886 frames contain the first recorded host latch of that source and 14
  contain a repeated host latch. All host latches, including unselected encoded
  frames, count here. Callback time orders them, with record sequence breaking
  exact-time ties. Duplicate system-frame latch identities fail validation.

The 1,886 first-host-latch samples have whole-interval p50/p95/p99
1.578201 / 2.317697 / 2.816858 ms. The 14 repeated-host-latch samples have
14.336338 / 14.490155 / 14.490155 ms. Their tiny population is descriptive,
not a stable population-tail estimate. Two host repeats were first appearances
in the selected history, explaining the different counts above.

Reuse does not account for every long host interval: eight first-host-latch
samples exceed 10 ms wake-to-acceptance, seven exceed 10 ms GPU-done-to-latch.
The largest first-latch sample (encoded 1623/app 1620) takes 14.604674 ms total,
including 3.921089 ms begin-to-delivered and 10.179072 ms GPU-done-to-latch.
This is observed association, not evidence of the underlying scheduling cause
or a claim that a specific optimization removes it.

Signed phases and interpretation
--------------------------------
Actual wake minus requested wake is p50 0.079266 ms, p95 0.199671 ms, with
minimum -0.020657 ms. Early wake is permitted as a signed phase; it does not
make any of the sequential elapsed-time stages negative.

Compositor predicted display target minus accepted input is p50 48.563283 ms,
p95 57.958869 ms. The corresponding source-layer target lead is p50 48.509579
ms, p95 57.909186 ms. These target timestamps describe prediction phase; they
are not actual waiting, photons, measured end-to-end latency, or an available
48 ms removable budget. They must not be added to client receive ages.

Application delivery can precede GPU completion, and one application frame can
be latched repeatedly. The wake/begin ABI values are WAKE_UP=0 and BEGIN=1 in
u_timing_point. The exact acquisition's app_pacer.cpp, codec_source_trace.h and
compositor.cpp hashes must match this analyzer's source-overlay helpers before
their semantics are used. Callback-provided when_ns and sampled observed_ns
are retained separately; maximum observation lags are 92,082 ns for prediction,
14,590 for wake, 8,487 for begin, 5,308 for delivery, 13,391 for GPU done and
74,186 for latch. Source instrumentation adds overhead.

Primary upstream contract references (checked 2026-10-09):
https://monado.pages.freedesktop.org/monado/structu__pacing__app.html
https://monado.pages.freedesktop.org/monado/u__pacing_8h_source.html

Validation and reproduction
---------------------------
host-timeline-validation-20261009.json binds the complete 306-test Python
suite. Seven focused tests cover exact telescoping, clock-domain translation
independence, warm selected-source history versus unselected prior host latches,
missing/duplicate/regressive callbacks, unsupported projection layouts, exact
integer fields and negative/signed phase handling. The analysis requires a
complete, zero-drop source footer and exact selected stereo trace admission.
Independent read-only transport-team review found no blocker in identity joins,
callback uniqueness, clock separation, history populations or phase wording.

From repository root (use new output paths; files are exclusive-created):
python3 prototypes/vr444/experiments/host_timeline/validate.py \
  --out /tmp/host-timeline-validation.json
python3 prototypes/vr444/experiments/host_timeline/analyze.py \
  /home/blux/spatial-bridge/validation/results/cpu-sub15-live-20261009T051345Z/05-closest-traced \
  --out /tmp/host-timeline.json
python3 prototypes/vr444/experiments/host_timeline/plot.py \
  /tmp/host-timeline.json --out /tmp/host-timeline.png

The PNG shows actual recorded intervals and three exact individual-frame
decompositions. Its separate figure receipt binds the plot source and result.
All samples, callback lags, worst examples and validation receipts are retained.
No headset operations, production promotion or speedup measurement occurred.
