Compositor research handoff, 2026-10-09

No new headset result is established. All device operations remain suspended.
The original live sequence stopped at run05; its failed summary is preserved.
The research branches repair diagnostic contracts, add opt-in capture, and
bound selection alternatives using immutable recorded evidence. Runtime
policy and production qualification limits are unchanged.

catalog.json pins 24 separately pushed approach branches and 66 notes,
validation or analysis artifacts at full immutable Git commit IDs. It is
the machine-readable branch/artifact index. Each artifact is read from its
own commit, never silently compared against later changed source files.

Historical integrity and fresh execution are separate:

  python3 prototypes/vr444/experiments/compositor_qualification/run.py --mode history --out /tmp/compositor-history.json
  python3 prototypes/vr444/experiments/compositor_qualification/run.py --mode python --out /tmp/compositor-fresh-python.json

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
has 331 passing tests, including eight catalog/runner tests covering omitted
or duplicate approaches, altered artifacts/sources, malformed digest maps,
external source references, exact known-gap declarations and all-skipped
test output. A transport-team independent read-only review found no blocker.

Separate final receipts rechecked all 1,492 original acquisition files in
five manifests and matched all 24 branch heads on origin to their catalog
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
