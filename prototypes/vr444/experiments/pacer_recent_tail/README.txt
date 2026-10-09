Opt-in recent-feedback pacer tail experiment

Measured motivation, not a predicted speedup:
The ready baseline's1894 warm stereo submissions measured18.629/21.458ms p50/p95
from earliest eye receipt to successful submission. Both-ready to selection was
14.371/18.143ms; the previous render fence wait was0.0097/0.0156ms. Exact selected
source/pacer joins show a30.384/33.269ms safe-present-to-decoded estimate while
host app wake to accepted compositor input was1.661/2.502ms. Stage percentiles
are not additive. The committed observed-host.json preserves these exact host
joins' input hashes; full raw acquisition remains root-owned. Existing traces
lack the underlying feedback sample history, so they cannot prove how much of
the budget is stale or how this experiment would alter live pacing.

The original estimator keeps two alternating5000-slot banks, swapping when a
stream0 feedback frame ID is divisible by100. It selects index floor(n*.995)
of positive decoded-minus-scheduled-present intervals, then adds client margin.
Those intervals include scheduling/encoding/transport/decoder/publication work
and clock conversion. They are not pure decoder durations. Each frame currently
uses the maximum eye feedback received in that bank before its swap; a late
other-eye feedback may reach the other bank. This experiment retains that input
contract and does not claim complete paired-ready samples or clock certainty.

Controls (WIVRN_CODEC_TESTS only):
  WIVRN_CODEC_TEST_PACER_WINDOW_FRAMES=900
  WIVRN_CODEC_PACER_TRACE=/absolute/new/pacer-trace.jsonl
  WIVRN_CODEC_TEST_PACER_TAIL_TRACE=/absolute/new/pacer-tail.jsonl
Missing/empty/0 window leaves original estimator behavior. Nonzero values must
be360..1800 and require both new sidecar and existing predictor trace. The old
harness deliberately scrubs inherited test variables; root must add an explicit
opt-in arm. No current production or performance-promotion path is enabled.
The sidecar is exclusive-created, bounded4096 worker updates, and exports after
worker join. Missing footer/dropped updates/invalid or reset-raced snapshots
fail its separate audit; full source/candidate/publication gates stay unchanged.

The age window counts frame IDs relative to newest valid feedback, not wall
seconds. Only the last W frame IDs qualify. At least128 distinct valid recent
frames are required; otherwise the unchanged full-bank tail is used. The same
99.5% order-statistic index and configured nonnegative margin are retained.
Margin conversion reads IEEE754 storage bits with integer arithmetic to avoid
native -ffast-math nonfinite assumptions; it truncates exact configured margin
to integer nanoseconds (default1ms is exact). Negative, nonfinite and >20ms
interactive values are rejected without changing the active budget.

Supported recent-window decreases are at most1ms per newly advanced feedback
watermark; increases apply immediately. Same-newest updated feedback can raise
but cannot lower the budget. Sparse fallback uses its full-bank tail immediately
when the watermark advances. A worker batch counter prevents spurious CV wakes
from replaying a bank. Future frame IDs relative to the actual issued boundary,
duplicate IDs, regressing newest feedback, arithmetic overflow and invalid
bounds refuse the update. Reset clears both banks and advances an epoch; a
copied earlier bank cannot overwrite the budget. The current budget is retained
until a valid new bank is admitted. Stop is synchronized under compute_mutex
before notification so enabled worker destruction cannot lose its wakeup.

Sidecar audit:
  python3 prototypes/vr444/experiments/pacer_recent_tail/analyze.py TRACE \
    --sha256 <explicit-input-sha256> --out NEW.json
This checks exact fields, coverage, typed bounds, source-produced populations,
watermark/epoch/batch and budget continuity, fallback/slew arithmetic. It does
not recompute raw feedback percentiles: raw_feedback_quantiles_recomputed=false.
No latency, photon, quality or promotion verdict follows from this telemetry.

Offline qualification:
- ASan/UBSan helper with native -ffast-math and2000 independent sorted-reference
  randomized populations; sparse/late/future/duplicate/backstep, startup, bounds,
  nonfinite margins, overflow, same-watermark and trace-capacity negatives.
- Actual unmodified pacer.cpp/pacer.h compiled against minimal isolated external
  API stubs under ASan/UBSan and TSan: startup fallback, slow decrease, immediate
  increase, spurious wake, duplicate bank, backstep refusal, reset and stop.
  This is actual worker/locking code, not actual Monado/device execution.
- Actual configured host native translation unit ON and OFF syntax checks with
  source/header copies in a temporary directory; retained native tree untouched.
- Five strict sidecar-audit methods, including subset quantile greater than full
  quantile (valid), support/epoch/watermark mismatches and incomplete telemetry.
-94-file forward/reverse patch and manifest parity, including frozen shader
  handling inherited from the root reconciliation workflow.

Paired live test plan for root (after separate host build/seal):
Use baseline0, window900, window900, baseline0 with the same APK, scene, geometry,
Q/output/selector, host binary and full diagnostics. Start fresh processes/banks.
Keep source/pacer/candidate/publication traces enabled for every arm and collect
new tail sidecar only in enabled arms. Capture startup and warm populations
separately. Verify actual refresh/focus, sample count/age/fallback/update history,
selected source identities, cadence, decoder drops, and original strict gates.
Any target regression remains a failure, as in the preserved RRB CPU diagnostic.
Do not relax target/ownership or validity contracts to make this arm pass.
Compare readiness residency and budget trajectories first; repeated untraced
qualification requires a separately authorized later design because this mode
requires tracing. No cadence or sub15 success is presumed.
