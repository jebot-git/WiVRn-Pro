Rooted Quest Pro research review smoke, 2026-10-09
================================================

The user reopened headset testing after the six-hour research window. ADB serial
230YC01D8W000E at 192.168.1.105:5555 was checked before staging. This is the rooted
Quest Pro; these standalone programs ran as the ordinary ADB shell user and did
not need root. All five probes returned zero with complete successful outputs.

Observed on Adreno (TM) 650
  - Timeline semaphore and synchronization2 features queried supported; this
    capability query enabled neither feature. Image-format-list advertised.
  - Host-image-copy and external-host-memory extensions not advertised.
  - AArch64 NEON: 271 roundtrips, 65 SIMD boundary cases, 10,000 mutations;
    scalar/SIMD canonical equality and unchanged rejected output passed.
  - RRB32 band views: 733 stereo cases, 576 direct RGB oracles, 2,000 differential
    corruption cases, 300 reuse calls; worker counts 1/2/4 and the 4MP eye bound.
  - Private Vulkan consumer: two captured stereo profiles (lossless/360 Q1 and
    paired-a/1350 Q8), nine arms, three warmups and one recorded repetition:
    72 valid invocations and 18 omitted-right-eye controls. All image bytes and
    first-consumer samples matched the exact same-device reference. Malformed
    pair, shape, and applicable late-decode failure gates also passed.

The band-view rejection count was 6,248 versus 6,256 in the prior host record.
A source audit found 6,240 fixed assertions plus four buffer-alias assertions
per populated worker scratch. Dynamic scheduling populated two workers in this
Android invocation (inferred from the total), versus four in the host record.
The difference is conditional alias-test coverage, not accepted corrupt packets.
The host-only reproduction is retained under results/band-rejection-audit.
Future suites should populate scratch deterministically or expose that count.

All 41 uploaded files were SHA-256 checked on the device before execution. The
runner used a fresh owned /data/local/tmp directory and on-device timeouts.
Cleanup returned zero and directory absence was verified. Battery rose from
8% to 9% while AC charging, temperature remained 26.0 C. No APK was installed,
no compositor was launched, and no system setting was changed by the runner.

Evidence and replay
-------------------
results/plan.json pins the artifacts and argv. results/device-run contains the
unaltered collection receipt and command logs, including capability and test
stdout. run.py is the exact executed wrapper. Its receipt status "completed"
means collection completed, not a test-pass decision; audit.py checks the actual
outcomes and hashes and writes audit.json. Its build_provenance entries link each
binary to its source team's prior qualification receipt; the plan's GPU source_head is
only GPU context and does not describe the two transport executables.

The portable GPU probes hardcode headset_used:false or a host-only scope string.
Those raw outputs remain unchanged. The outer receipt/audit records their actual
Android execution and overrides those static labels for this run's provenance.
Do not use the host full-corpus auditor on this two-profile Android subset.

Original acquisition:
  python3 prototypes/vr444/experiments/headset_review_smoke/run.py \
    validation/results/headset-review-20261009/plan.json \
    --out validation/results/headset-review-20261009/device-run

The plan references exact local build/corpus paths. Replays need those artifacts
or rebuilt byte-identical copies, a currently verified ADB target/serial, and a
new output directory. Preserve any changed plan as a new acquisition. No APK,
Vulkan feature enabling, native compositor integration or wireless transport
measurement is implied by successfully invoking this runner through wireless ADB.

Offline audit of the archived copy (choose a new output filename):
  python3 prototypes/vr444/experiments/headset_review_smoke/audit.py \
    prototypes/vr444/experiments/headset_review_smoke/results/plan.json \
    prototypes/vr444/experiments/headset_review_smoke/results/device-run \
    --out /tmp/headset-review-audit.json
The original input files must remain available. Alternatively, --input-root DIR
uses artifacts laid out by the plan's remote-relative names and verifies the
same hashes. Audit validation rejected a nonzero SIMD exit despite collection
status completed; it also rejected a false GPU first-consumer exactness flag
after the corresponding log hash was updated. A relocated set of all 41 inputs
passed, and an altered executable failed. These checks make no device calls.

Scope and next integration
--------------------------
These are capability and correctness observations. One recorded repetition,
three-submit poison paths, uncontrolled device scheduling, private compute
consumption and no OpenXR presentation cannot establish comparative performance,
p95 latency, optical latency or the sub-15 ms target. Raw clocks are retained only
as diagnostics. No timing threshold was evaluated and no architecture promoted.

The RRB32 mapped-output path is now a demonstrated device correctness candidate.
Native integration still needs negotiated payload grammar, atomic complete-pair
publication, owned buffer/image lifetimes through final render reads, capability
fallbacks, and real receive-to-successful-xrEndFrame timestamps. The research PRs
need the findings in PR-REVIEW.txt addressed before their importers admit live
evidence. The neural tracks additionally need an actual native adapter and a
properly separated research corpus; this smoke supplies neither.
