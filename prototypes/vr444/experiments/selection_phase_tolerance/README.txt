Historical selection boundary census; work stopped on standalone rescope
======================================================================
The manifest-bound four-arm RRB4 pacing bracket has a coherent newer pair in
1619/1898,1573/1889,1340/1897,1284/1910 warm observed histories. The current
one-period future horizon admits a newer pair in1001,770,699,653 snapshots;
100us extra admits1575,1525,1249,1226;500us admits1615,1573,1327,1284.
These are separate tests of each actual snapshot, not an alternative sequence,
throughput, missed-frame or latency prediction. Prior bounded-newest live tests
did not demonstrate a win; that result remains unchanged.

Selected source prediction targets already match runtime targets within about
15us at the medians. Selection occurs about19ms before the runtime target.
A newer frame normally has a source prediction target about one11.111ms period
later. Selecting it could lower encoded-frame age while advancing pose prediction;
no pose/image-quality equivalence follows. Minimum observed ready residency was
0.10..0.47ms, so the17..20ms p95 residence cannot safely be subtracted uniformly.

Root authorized exploring explicit100us/500us tolerance, but the user then moved
all new work to a standalone codec repository with a required sub5ms source-image
ready to reconstructed-receiver-image ready target. No selector helper, native
wiring, native build, device operation or tolerance performance test was made.
This branch archives the census only; it is not a selected implementation.

Reproduce from repository root using the preserved original campaign:
  python3 prototypes/vr444/experiments/selection_phase_tolerance/census.py \
    /path/to/native-pacer-abba-retry --out /tmp/historical-selection-census.json
The script pins all raw reads to each final manifest and checks them afterward.
It imports historical summary gate status, rather than rerunning full trace
validators. Full earlier validation is archived in pacer_abba_audit. Its old
sub15 clocks/requirements must never be substituted for the new standalone gate.
