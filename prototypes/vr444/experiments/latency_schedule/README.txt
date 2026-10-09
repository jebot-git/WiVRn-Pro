Latency scheduling experiment (offline only)
============================================
Run from the spatial-bridge repository root:
  python3 -m unittest discover -s prototypes/vr444/experiments/latency_schedule -p 'test_*.py' -v
  python3 prototypes/vr444/experiments/latency_schedule/replay.py --output validation/results/latency-critical-0.13.0/scheduling/replay.json

Python standard library only. Default --root is resolved from this file's location;
override --root when the experiment is copied outside its repository layout.
Inputs: native-codec-comparison-0.12.0/{04-custom,05-custom}/hashes.json,
client/codec-test-present.jsonl and client/codec-test/decode-*.jsonl.
Every consumed JSONL is checked against its existing run SHA-256 manifest.
Inputs are never modified. A missing or contradictory identity/hash raises an error.

Measured observations are first-packet receive, CPU publication, xrEndFrame return,
and decoder-local durations, joined on exact frame/warp/eye. Percentiles use nearest
rank, include startup, and are not sums of independent percentile distributions.

The frontier is a counterfactual using only submitted pairs with known readiness.
It is deliberately named incomplete_observed_pairs_unbounded_residency. Its cutoff
is xrEndFrame return minus an assumed margin, NOT a measured selector timestamp.
It ignores actual three-entry buffer residency and never-submitted candidates.
Its signed age reduction is the change in first-packet age, NOT latency saved.
TTL can reject fresh admissions while leaving a stale prior pair on screen; counts
explicitly show these stale repeats. It cannot prove optical motion-to-photon.

DeadlinePairs is a separate synthetic bounded publication-policy model. It is NOT a
replacement implementation or exact emulation of live StereoAdmission. Eye readiness
and deadline are inputs. At most two pending frame IDs exist, protected complete pairs
survive orphan floods, and publication requires both eyes and an identical immutable
frame/warp/geometry/kind/deadline identity. Selection retires older identities. The
model has no active GPU job and makes no prediction about reordered GPU workloads.

See validation/results/latency-critical-0.13.0/scheduling/REPORT.txt for findings,
prioritized implementation experiments, references and measurement limitations.
