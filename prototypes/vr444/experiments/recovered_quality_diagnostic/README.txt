Recovered first quality1152 run: diagnostics, not repaired acquisition

The original chroma-grid run failed because client archive transfer timed out.
Its failed summary, cleanup, hash manifest and campaign are preserved. Root later
recovered the client archive into a different directory. audit.py binds these two
inventories separately and verifies every extracted recovery file against the
compressed archive bytes. It performs no ADB, network or graphics operation.
plan.json pins the original hashes, recovery receipt/summary and native build.

All 30 saved eye payloads (15 pairs) reconstruct exactly with the separately
hash-pinned sequential scalar oracle, and equal both host reference/source and
recovered client RGBA. Fourteen of these sampled pairs were actually presented.
The actual recovered CPU4 telemetry has 1,327 complete decoded pairs, same paired
frame/warp/generation/time, and zero paired failures. Fourteen pending pairs were
dropped, as the retained native summaries report. These rows were missing from
the failed collection; absence in that summary was not a receiver failure.

Direct original application -> recovered submitted images FAILS the necessary
zero-code protected comparison in all three saved stereo pairs (frames90/270/360).
Each eye has 69,612 protected pixels: 754 changed RGB pixels left, 1,603 right, and
maximum absolute code error13 in each. Alpha is opaque. The original-to-host
source stage and all70,806 required unit-footprint halo cells are exact. Therefore
the decoded codec path is exact, and the later client image reconstruction is
where this diagnostic first observes the mismatch. The same-device identity
reference has not been run by this auditor; no filtering/gamma cause is presumed.
The failed acquisition remains failed and no full quality result is conferred.

Recorded cost, startup and quality capture included (nearest-rank p50/p95):
  Shared stereo CPU4 decode, counted once: 3.11026 / 4.46453 ms, 1,327 pairs.
  Left eye staging copy: 1.98292 / 3.13328 ms.
  Right eye staging copy: 1.96031 / 3.15245 ms.
  Left upload outer wall: 7.11932 / 10.1989 ms.
  Right upload outer wall: 7.08599 / 10.1524 ms.
These per-eye and shared clocks overlap; they must not be added. This quality
profile is not a latency measurement or production performance comparison.
Host timing and client timing are never subtracted.

Separately, retry-first-source-map.json is the full unchanged source_mapping
formal audit of the fresh successful retry chroma-grid-r1. It passes15 actual
mapped/encoded stereo samples and structurally audits3 submitted capture pairs,
with all protected original-to-post-host source texels exact. It does not assert
that the downstream submitted original-pixel comparison passes. The saved command
has explicit run/native/app receipt hashes and emits outside the live campaign.

Four unit groups cover manifest/path/hash/extra-file changes, changed rereads and
symlinks, shared-clock count-once plus peer splices, and invalid timing values.
Result and source hashes are recorded in qualification.json. Build receipts are
consumed, not reconstructed; no rebuild or hostile-host authentication is claimed.

Independent compositor-agent read-only review found no material scope/provenance
blocker. Exact typed numerical telemetry is future hardening; this finite report
uses hash-bound actual native rows and does not promote a failed acquisition.
