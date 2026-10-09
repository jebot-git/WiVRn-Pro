Compositor research handoff, 2026-10-09

No new headset result is established. All device operations remain suspended.
The original live sequence stopped at run05; its failed summary is preserved.
The research branches repair diagnostic contracts, add opt-in capture, and
bound selection alternatives using immutable recorded evidence. Runtime
policy and production qualification limits are unchanged.

catalog.json pins 27 separately pushed approach branches and 77 notes,
validation or analysis artifacts at full immutable Git commit IDs. It is
the machine-readable branch/artifact index. Each artifact is read from its
own commit, never silently compared against later changed source files.

Historical integrity and fresh execution are separate:

  python3 prototypes/vr444/experiments/compositor_qualification/run.py --mode history --out /tmp/compositor-history.json
  python3 prototypes/vr444/experiments/compositor_qualification/run.py --mode python --out /tmp/compositor-fresh-python.json
  python3 prototypes/vr444/experiments/compositor_qualification/run.py --mode host --build-dir /tmp/compositor-fresh-host-build --out /tmp/compositor-fresh-host.json

The history mode verifies pinned artifact bytes and declared in-repository
and virtual-overlay source bindings. Absolute temporary executables and
native build-cache inputs remain explicitly unverified. Compiler binaries,
original acquisition files and remote branch heads are not verified by
this mode. It does not rerun old sanitizer, C++, Android or native checks.

One historical source gap remains visible: phase-frontier-validation has
the pre-layout plot_phase_frontier.py hash. That receipt was produced before
the final footer placement edit. The already archived phase-frontier figure
receipt explicitly documents this ordering and binds the final script,
analysis JSON and PNG. The catalog requires both exact hashes and the known
gap explanation. It reports incomplete_source_history; it does not rewrite
the old receipt or relabel full historical source integrity as passing.

Fresh Python mode additionally runs the entire current validation suite,
requires nonzero execution and plain OK without skips, and rehashes all
files in the declared source directories before/after. The completed run
has 353 passing tests, including eleven catalog/runner tests covering omitted
or duplicate approaches, altered artifacts/sources, malformed digest maps,
external source references, exact known-gap declarations and all-skipped
test output, host success diagnostics and complete sanitizer-stage population.
A transport-team independent read-only catalog review found no blocker.

Host mode additionally rebuilds eight current standalone C++ test programs
under ASan/UBSan: legacy selection, render attempts, fractional horizon,
ready successor, cardinality, source novelty, publication ledger and host
pacer recorder. Publication and pacer concurrency tests also rebuild/run
under TSan, for ten fresh sanitizer executions. Compiler argv/version/hash
and binary hashes are recorded; any diagnostic, missing complete success
marker or missing/duplicate stage rejects the run. Leak detection is disabled
under ptrace. This is not a linked WiVRn build, actual native-constructor
execution, Android compile, GPU test, performance run or device operation.
Those historical checks retain their prior scope and are not silently
claimed as freshly rerun. Build/output paths must be outside this worktree.
An initial runner smoke rejected pacer compilation because its explicit
WIVRN_CODEC_TESTS guard was missing; the fixed argv and guard regression
precede the complete final run. No partial run was recorded as passing.
An independent GPU-team read-only review found no host-mode gate blocker.

Separate final receipts rechecked all 1,492 original acquisition files in
five manifests and matched all 27 branch heads on origin to their catalog
commits. These are read-only integrity checks, not fresh device evidence.
The primary fresh-suite receipt still honestly marks those separate stages
as outside its own scope. Output receipts are copied into this directory
only after execution completes; their creation is not part of the recorded
source snapshot.

Most useful results and boundaries:

1. TRACE-GAP-REPAIR and RENDER-ATTEMPTS distinguish submitted rows from
   intervening empty render attempts. Legacy run05 reanalysis cannot invent
   terminal attempts. Schema3 accounts scene start through last submission,
   with terminal tail explicitly unknown. Original failure is immutable.

2. HORIZON-RESEARCH and READY-SUCCESSOR provide standalone opt-in selection
   prototypes. Recorded counterfactual receive age and cadence are coupled;
   a better percentile can come from fewer new frames. Successor support is
   independently source-joined. Neither prototype is a production selector.

3. PACER-TRACE/PACER-CAPTURE and PUBLICATION-LEDGER/PUBLICATION-CAPTURE add
   default-off host/decoder diagnostics, with typed provenance and promotion
   rejection. Publication coverage ends at the last exported checkpoint.
   PACER-TRACE requires the later compositor-pacer-init constructor-order
   fix: initialize the recorder before its worker thread is constructed.

4. TIMELINE and host_timeline/README split only same-clock intervals and
   preserve exact identity joins. Most observed selected-frame receipt age
   accumulated after decoder publication, but causes and removability are
   unresolved. Stage percentiles must not be added. Predicted display targets
   are phase metadata, not photons or a guaranteed removable wait.

5. EXACT-PHASE proves the minimum encoded-receipt p95 across every integer
   shift from 0 to 11,111,111 ns within an optimistic fixed model: zero
   receive-to-publication delay, fixed original targets and warm history,
   sufficient full-window 88 Hz count of 1,875, and known recorded supply.
   One-horizon encoded minimum is 19.241095 ms. Removing the target ceiling
   gives 14.852189 ms at phase 4.387188 ms, while requiring distinct source
   generations gives 15.924690 ms at phase 5.488282 ms (both two-horizon and
   unbounded tested models). Exact-model results do not predict runtime
   feedback, ownership, earlier network receipts or lower-count short spans.

6. SOURCE-RECEIPT-AGE prevents a newer encoded identity from resetting its
   earliest known application-source receipt. Actual warm p95 becomes
   27.037346 ms versus encoded 26.927659 ms, with 14 older known origins.
   It rescored the exact-phase winning paths without reoptimizing this new
   diagnostic. It is not complete input/application or optical latency.

7. SOURCE-AGE-BOUND derives source-p95 optimum brackets from pointwise age
   dominance and legal rescored witnesses. Within the same exact-phase
   model, one-horizon encoded source age lies in [19.241095, 19.311512] ms;
   unbounded-target encoded in [14.852189, 14.974637] ms; both source-distinct
   models in [15.924690, 15.998856] ms. These are inclusive bounds, not new
   exact optima. A separate rendered figure and receipt show all intervals.

8. CADENCE-COVERAGE checks the native first-to-last-new-frame interval against
   the entire observed warm presentation span. All five original captures
   have zero held endpoints and identical rates. Seven synthetic regressions
   show why this cannot be presumed for paths that omit deadlines. Missing
   submissions and observed held frames stay distinct. The policy is unchanged.

9. ENDPOINT-ENVELOPE removes the sufficient full-span count assumption for
   five explicit phases by checking all 509,001 observed endpoint intervals
   of at least ten seconds. Exact good-only advancing chains bound necessary
   p95 counts. At phase zero, one-horizon encoded and both source-distinct
   models fail every interval, even at receipt-floor readiness. The three
   intermediate phases remain inconclusive; no legal mixed-age path or
   all-phase endpoint conclusion is claimed. Tests and a separate figure
   show these limits without changing the earlier exact-phase result.

Native integration remains outstanding and owned by the integration team:
regenerate integration.patch and overlay-manifest, include the pacer init
fix and selected diagnostics, then perform a linked native build and full
qualification before any future authorized headset test. This branch does
not touch shared caches or those integration artifacts. No historical or
fresh Python pass permits bypassing diagnostic-only promotion restrictions.

All source, tests and research artifacts are committed on isolated branches.
The catalog is descriptive evidence, not authentication of code or receipts.
Its generator uses explicit milestone IDs; changing the catalog is a new
reviewable change, never automatic acceptance of a floating branch head.

Final committed-input binding and cross-team handoff:
fresh-handoff-inputs-20261009.json independently binds all 341 unchanged fresh
suite input files to immutable handoff commit fe9dae00c4c8c89dad45502f2908b6c901800737.
It identifies exactly three output/integrity receipts replaced after execution
and two subsequently added cross-team reference documents. Later notes/receipt
additions do not silently become freshly executed sources. CROSS-TEAM-HANDOFF.txt
and cross-team-pointers-20261009.json pin the separate GPU/transport results and
supplementary mutation campaign; no merged source tree or summed timing is claimed.

Historical narrative errata discovered in the final read-only check:
HORIZON-RESEARCH's admission count is 9,399, not its earlier transcribed 9,522;
READY-SUCCESSOR's advancing-choice count is 4,045, not 4,037. Their own archived
successful sanitizer stdout already records the correct counts, and the fresh
host run independently agrees. Current note copies are corrected, while the
historical catalog retains immutable old notes. narrative-errata-20261009.json
pins both supporting receipt blobs and the corrected current notes. No test
implementation, counterfactual performance result or qualification gate changed.
These note errors are separate from the already explicit historical plot-source
binding gap; artifact-byte verification does not assert narrative infallibility.
