Bounded Quest GPU comparison
===========================

This follow-up uses the existing, unchanged Android mapped-consumer binary and
the pinned 576x640 Q1 lossless/360 and Q8 paired-a/1350 true stereo captures.
Root owns all device execution. triage.py never invokes ADB, changes a setting,
or builds/executes native code. It emits the exact 39-file plan accepted by the
historical headset_review_smoke/run.py and audits that runner's raw receipts.

Run design
----------
Two independent processes, each --reps 7, three warmups, nine rotating arms.
Each process: 126 recorded and 180 total valid arm invocations, 18 independent
right-eye omission controls. Total: 252 recorded / 360 valid / 36 omissions.
Each device process has a 180-second KILL timeout; host bound is 195 seconds.
Any timeout, nonzero command, stderr, missing profile/arm, exactness failure,
hash mismatch or observed capability drift fails the audit. Earlier completed
samples cannot turn a partial process into a passing comparison.

The two runs are deliberately exploratory. Report seven-value medians/min/max
and same-repetition ratios, separately for each process and each captured pair.
Do not pool them into a claimed p95 or generalize to all captured or live scenes.
The next measurement is conditional on what these inexpensive runs establish.

Usage (host only; root separately executes the emitted plan):
  python3 prototypes/vr444/experiments/android_gpu_comparison/triage.py plan \
    --out /tmp/quest-gpu-triage-plan.json
  python3 prototypes/vr444/experiments/android_gpu_comparison/triage.py audit \
    --plan PLAN.json --run-directory RAW_DIRECTORY --out AUDIT.json
  python3 -m unittest discover \
    -s prototypes/vr444/experiments/android_gpu_comparison -p 'test_*.py' -v

--input-root permits replay of retained artifacts under their device-relative
paths; otherwise each original plan-local artifact must still be present.
The immutable build receipts and every source binding are rechecked in Git.
The runtime input population is exactly the earlier smoke's files minus its two
CPU test executables; their original SHA-256s also bind all eight SPIR-V files.
Pinned full RRF/RRB manifests are filtered to the exact four eye records and
sixteen independent row-block records before the unchanged nine-arm consumer
auditor runs. The raw host-only label remains untouched and is explicitly
superseded only for execution location by the Android wrapper's provenance.

Prior evidence and interpretation
---------------------------------
The one-repetition smoke is correctness evidence, not comparative performance.
Its diagnostic decomposition nevertheless motivates this bounded experiment:
every direct mapped decode observation was 32.55--93.28 ms, while staged complete
preflight/decode was 0.61--1.63 ms. This is inside the CPU decompression interval,
not merely a slow shader. All requested default/cached arms actually selected
the same memory type 4, flags 15. Flags alone do not explain CPU mapping speed.
Do not attribute the difference to cacheability, coherency or LZ4 implementation
without a matched heap/staging decode experiment on this device.

The stronger immediate candidate is staged RRB32, retaining CPU RGBA upload and
staged whole-RRF as controls. Direct mapped output remains an important negative
control: desktop wins do not select the Quest implementation. The RRB shader
spans were roughly 0.27--0.31 ms against 0.81--1.05 ms for whole-RRF in that smoke,
but total image readiness plus first consumer must decide the practical choice.

Every timed invocation includes three synchronous submit/fence cycles: poison,
decode/image readiness, then private compute sampling of both SRGB images into
host-visible floats. Queue timestamps measure queued device spans; wall clocks
also include host work, driver overhead and scheduling/fence delays. Some smoke
fence/consumer waits were 8--10 ms despite sub-millisecond GPU spans; the source
does not identify their cause. Thermal/DVFS, charging, display/OS work and CPU
placement remain possible influences rather than measured explanations.

Nine separate logical-device sessions and all shaders/images are constructed
before the case's measured loop. Large mapped-float snapshots, independent
readback/color oracle (including per-pixel CPU math), JSON serialization and
setup are outside the per-invocation clock; they consume elapsed time and can
perturb later cache/thermal/scheduling state. The original two-profile smoke
took 59.70 seconds overall. The whole process duration is not image latency.
Poison is intentionally included; subtracting its interval does not undo its
effects. Median stage times are not additive to an outer-clock quantile.

No Vulkan optional extension is enabled by the candidate. Core Vulkan 1.1,
graphics+compute+timestamp queue, compatible coherent host mapping (cached arms
also require HOST_CACHED), checked storage/transfer/sample image formats and
mutable SRGB sampling are required. All nine arms must pass; there is no silent
unsupported-arm fallback. Timeline/sync2/host-image-copy capability queries are
inventory only. Server preprocessing, encoded-size growth, socket delivery,
reassembly, live ownership/retirement, OpenXR submission and optical latency
are outside this experiment. No development branch is promoted to production.
