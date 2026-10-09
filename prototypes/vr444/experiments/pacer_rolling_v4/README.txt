Rolling pacer v4 integration
===========================

This branch starts at root 3738d8e7b744d3c7f3c602737becab8f67d0de9b and collects
the independently qualified rolling candidate cc10127b9a69805abbc13f2ea6f192a15650069f.
It wires --pacer-feedback-bank alternating|rolling into native_compare.py with
default alternating, records the exact request in provenance, and passes it to
the tail evaluator after process teardown. Existing pre-device validation and
environment clearing cover this option. Tail failure still fails the run and
all other strict evidence/latency gates remain intact.

The 97-file overlay is reconciled against the exact prior manifest and verified
baseline subset /tmp/sub15-v3-before97. There are no new native paths; the retained
native baseline argument is read-only and contributes no new bytes. The only
native changes from root are pacer.cpp, pacer.h and codec_pacer_tail.h. The source
mapping/foveation and client sources remain byte-identical to root's v3 tree.
This supersedes the candidate README's deliberately unreconciled 95-file union.

Reproduce reconciliation from this checkout with a fresh receipt path:
  python3 prototypes/vr444/experiments/dual_goal_workflow/reconcile_overlay.py \
    --prior-commit 3738d8e7b744d3c7f3c602737becab8f67d0de9b \
    --baseline-subset /tmp/sub15-v3-before97 \
    --native-baseline /home/blux/spatial-bridge/.worktrees/wivrn-codec-tests \
    --receipt /tmp/v4-union-reconcile.json

Run offline parity and harness regression, with a new output directory:
  python3 prototypes/vr444/experiments/pacer_rolling_v4/qualify.py \
    --out /tmp/v4-rolling-offline
The runner reconstructs the baseline in that disposable directory, verifies both
text-patch and binary-inclusive overlay application, and runs the full integration
Python suite plus the pacer analyzer and native-campaign wrapper tests. Every
suite must report a nonempty test population and plain OK without skips. The
receipt binds commands, logs, exact inputs and before/after source hashes.
The recorded run passed 331 integration, eight analyzer and four campaign tests
(343 total), plus exact 97-file parity. All seven prior candidate receipt source
maps still match. The native manifest delta is exactly the three pacer files.
The generated patch retains an existing tab-indented context line that Git's
outer diff whitespace check reports as space-before-tab; it is a unified-diff
context prefix, not a new source indentation change. Non-patch edits pass the
whitespace check, and exact reverse/apply/hash parity passes.
Independent codec-team read-only review of the CLI, provenance, explicit evaluator
argument, early rejection and inherited-activation clearing found no blocker.

Original native sanitizer/worker/quantile/syntax and semantic-negative receipts
remain under pacer_rolling_snapshot/results; their source hashes remain valid.
The new integrated controls additionally test CLI default/explicit/invalid modes,
inherited bank/window/path clearing, and rejection before any device or process
command when rolling lacks its window or mandatory diagnostic controls.

No native binary was built or installed here. Root must build and seal a separate
v4 server/artifact receipt and matching source snapshot before live testing; the
v3 quality acquisition retains its exact existing build. No ADB, socket, GPU or
headset action was performed by this integration work. This is a default-off
diagnostic research candidate, with no rolling live latency or quality claim.

For the later bank comparison, keep a nonzero 900-frame estimator in both arms:
alternating/rolling/rolling/alternating. The original candidate README specifies
the bounded paired plan and telemetry limits. Root owns the live plan and renewed
headset-worn readiness; this source branch does not authorize or start a run.
