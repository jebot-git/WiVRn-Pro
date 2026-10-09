Actual RRB CPU4 pacing bracket, 2026-10-09
=======================================

Selection: retain the 900-frame recent-tail pacer as a default-off diagnostic
candidate. It passed both actual-device enabled runs and both controls with the
unchanged source, candidate/attempt, pacer, publication and codec gates. Both
its p95 and p99 were lower than both controls in this bracket. None met the
strict sub-15ms goal or the older latency target set; no latency promotion or
quality qualification follows.

Same recorded host binary/APK/runtime, RRB32 from VR444 Q8, four CPU workers,
internal block compression with no outer wrapper, after-fence closest-target,
cached CPU output, 90Hz requested, 30 seconds per fresh process. All four had
warm predicted periods close to11.111ms and their own PID remained FOCUSED
through the recorded warm submissions. The headset wearer confirmed readiness
before this campaign. No optical timing or exact displayed-pixel claim is made.

The initial campaign stopped before any arm because the preflight raw backup
exceeded30seconds. It remains separate. The retry used bounded compressed
backup and completed with unchanged build/analyzer hashes, all configuration
restored, dedicated app stopped, reverse absent and production package unchanged.

First-use full stereo after2seconds warmup, earliest eye receipt to recorded
post-successful-xrEndFrame submit, nearest-rank quantiles; milliseconds:

Arm             pairs     p50        p95        p99       unique Hz
0-before        1898     21.419     25.541     27.200      89.618
900-first       1889     20.638     23.540     24.361      89.662
900-second      1897     18.517     21.309     23.482      88.862
0-after         1910     18.178     24.017     25.659      89.533

This is neither per-eye pooling nor an average of eye quantiles. Global first
use includes prewarm history, so a carried/repeated frame cannot become a new
warm sample. No run has a repeat in the warm presentation summary.

Observed stage p95, same selected warm population; milliseconds:
Arm             receipt->bothready  bothready->selection  selection->xrEndFrame
0-before             10.355               20.150                 1.917
900-first             9.884               18.561                 1.847
900-second            9.745               17.022                 1.803
0-after               9.716               19.049                 1.850

Stage quantiles are not additive. Prior render-fence p95 is below0.016ms in
all arms. The post-xrEndFrame diagnostic-write tail has p95 0.074/0.070/0.056/
0.056ms. These are observed stage durations, not an estimator CPU-cost measure.
The selected host safe-present-to-decoded estimate p50 is36.859/33.956/31.939/
31.452ms. Host source/pacer joins use exact frame ID plus returned display target;
all host differences remain in the host clock. These estimates include more
than decoding and are not removable causal latency.

The two enabled sidecars revalidate against their finalized run manifests:
21updates (18recent,3fallback,5slewed) and20updates (18recent,2fallback,5slewed),
no drops/reset-raced/invalid records, no same-watermark decrease. Late recent
support is around500frames, because the two5000-slot banks alternate rather
than retaining one cumulative900-ID sample cohort. Missing eye feedback and
unrendered/unreported frames cannot be reconstructed from summary telemetry.

Compared with the full-bank tail from the SAME captured update (an input-level
alternative, not a counterfactual live run), the applied recent estimate is
at most1.689ms /0.853ms lower in the two runs. It can also be0.930/1.374ms higher
because the downward slew is deliberately conservative. The last deltas are
-1.007ms and0ms. Original raw feedback percentiles cannot be independently
recomputed; only source-produced summary arithmetic and coverage are replayed.

The control median improved3.241ms from before to after, and control p95
improved1.524ms. Every run also improves between its early/late halves. The
second enabled arm has the lowest cadence (still above88Hz), and its maximum
34.787ms exceeds both controls despite improved p95/p99. These observations
limit attribution. Four sequential exploratory runs do not establish a causal
speedup, steady-state overhead, reproducibility across scenes, or sub-15ms.

Next isolated scheduling candidate: a coherent cumulative feedback-ring
snapshot at the existing stream0/frame%100 trigger, instead of swapping the
two partial histories. Keep feedback clocks/admission,900-ID horizon,.995 tail,
margin,support/slew/epoch guards and target deadlines unchanged. Record exact
snapshot mode/depth/age and batch coalescing; do not label partially observed
eye feedback a complete stereo measurement. This addresses observed bank
cohort inconsistency, with no assumed improvement and no default activation.

Reproduce, using an unchanged checkout containing the exact recorded analyzers
and overlay (the audit checks their recorded hashes):
  python3 prototypes/vr444/experiments/pacer_abba_audit/audit.py \
    /home/blux/spatial-bridge/.worktrees/sub15-dual-goal \
    /home/blux/spatial-bridge/.worktrees/sub15-dual-goal/validation/results/dual-goal-20261009/v2-build/native-pacer-abba-retry \
    --out /tmp/new-pacer-abba-audit.json

The output is exclusive-created. The committed JSON binds every read by path,
size and SHA, checks the completed campaign and final per-run manifests,
independently recomputes coherent warm metrics and exact source/pacer joins,
and replays the enabled tail analyzer. Historical source/candidate/publication
PASS fields are reported from their hash-bound capture summaries; their entire
validators are not rerun here. Actual source/read bytes are rehashed after the
analysis, but the report is not a native rebuild attestation. All acquisition
files remain immutable/root-owned and are not duplicated in this small branch.

The actual four-arm replay passed. Two disposable-copy negatives also rejected
without emitting a report: a misjoined right-eye frame, and an invalid tail
update, each with its run-manifest hash repaired while leaving the saved summary
PASS. Their source/report bindings and outcomes are in validation.json. Original
acquisition files were never edited.
