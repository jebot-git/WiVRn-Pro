Offline source-clock and frame-target investigation (0.14)

No live source, protocol, harness, device or promotion-policy changes.

Run:
  python3 -m unittest discover -s prototypes/vr444/experiments/latency_clock -p 'test_*.py' -v
  python3 prototypes/vr444/experiments/latency_clock/analyze.py validation/results/latency-live-0.13.1/0*-*/ --out validation/results/latency-research-0.14.0/clock/analysis.json

analyze.py validates presentation identity and timestamp ordering, verifies input
manifest hashes, keeps only earliest successful coherent stereo submissions, uses
a two-second local-clock warmup, and joins sampled feedback by session/eye/frame.
Feedback from repeated/ambiguous first presentations is excluded. Timings must
telescope for the same frame; distinct stage-frame counters are never combined.
Both-eye joins and one-eye samples are counted explicitly. No promotion gate is
modified or satisfied by this analysis.

The local predicted target-minus-submit statistic is a difference between an
event and a prediction. The reconstructed encode-minus-submit uses host times
already converted into headset coordinates, with no measured synchronization
error bound. Neither is source scene age, hardware decode time or photon latency.

offset_bracket demonstrates the conservative causal interval available from one
three-timestamp probe under a constant additive clock and nonnegative delay.
It is not a replacement clock estimator or an uncertainty claim about old runs.
The test includes asymmetric delay producing a biased midpoint estimate.

Full interpretation, source trace, references and future instrumentation proposal:
  validation/results/latency-research-0.14.0/clock/REPORT.txt
