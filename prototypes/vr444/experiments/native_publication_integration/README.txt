Native publication/pacer integration — diagnostic milestone

Base:8e4d7a862591b3a5d2d9fc888da967fde0cb9165. Imported selected diagnostic
commits ff669ce7,23ac404c,7d3dfbe4,47a5677a,ced4c13e,be5fd0bd in that order.
This includes render-attempt accounting, immutable publication prefixes,
host pacer observations and the initializer-before-worker correction. Existing
schema1/2 successful-submission continuity repair remains intact. Both flags
are default-off; diagnostics still block production/latency promotion. A future
research decision may consider latency OR a separately qualified quality axis;
these diagnostic checks do not themselves satisfy either performance or quality.

The final source-overlay inventory/manifest has68 files; integration.patch
reconstructs the same61 text files and apply_overlay adds the7 frozen SPIR-V
binaries. The original64 before hashes are preserved. New pacer.cpp/.h baseline
bytes come from native84fad67091f36a0343e090388ad8708b258420f3; the other two new
headers have absent baselines. No codec/decoder/encoder implementation changed.
The shared source fixture was extracted from old counterfactual tests solely
to make native acquisition tests independent of unrelated offline models.

Fresh checks:229 Python tests, full Android stream translation-unit syntax with
WIVRN_CODEC_TESTS enabled and disabled, and exact patch/overlay parity pass.
See android-syntax-20261009.json and overlay-parity-replay-20261009.json. These
are not a linked host/server build, installed APK or live capture. Source fixture
and reconciliation hashes are additionally recorded in milestone-20261009.json.
Independent GPU-team read-only review found no default-off, ledger lifetime,
FIFO/checkpoint, initializer-order or manifest-population blocker.

Reproduce parity without touching the original native checkout:
  python3 prototypes/vr444/experiments/native_publication_integration/verify_overlay.py \
    --baseline-out /tmp/new-publication-baseline-subset \
    --out /tmp/new-publication-overlay-check.json
The baseline subset is deliberately incomplete; it proves the68 changed file
contract and is not a build tree. The first local subset is preserved at
/tmp/sub15-publication-overlay-baseline-20261009. The recorded upstream baseline
also contains upstream-working-tree.patch and preserved untracked dependencies;
Git84fad670 alone is insufficient for the original64 before hashes.

Build handoff: root owns a fresh isolated copy of the retained native source and
its dependencies. Verify the prior70-file build receipt before copying, then
verify each inherited overlay file against base8e4d7a86's after hash before
replacing it with this final68-file overlay. New original pacer files must match
this manifest's before hashes. Never run apply_overlay against the already
patched retained checkout: it correctly requires pristine before hashes. A
disposable full baseline may instead reset only the verified inherited overlay
files to the reconstructed before bytes, then use apply_overlay normally.

Do not run an unmodified copied CMakeCache/compile_commands/Gradle .cxx cache:
they contain absolute paths into the retained source. Reconfigure a NEW build
directory with the same recorded options/toolchain and dependency cache roots,
using the new source path. Cached dependencies may be read/reused; output paths
must stay in root's isolated build tree. Rebuild server/APK together and seal a
new receipt including all68 overlays, binaries/runtime manifest and config
set. The future runner backs up/restores11 config files (publication trace added).
The historical ten-file campaigns and their fixed replayers remain unchanged.

Paired live plan (root/device owner only; not executed here)
1. Fresh dedicated build/install provenance and11-file backup/restore check.
   One short untraced closest-target/cached-copy smoke, default flags off.
2. Two15-second closest-target/cached-copy diagnostic captures with all four:
   --candidate-trace --source-trace --pacer-trace --publication-trace.
   Require zero drops/invalid writes, schema3 attempt accounting, immutable
   publication checkpoint/history/source joins, exact pacer/source identity,
   coherent full stereo and final cleanup. Missing footer/prefix fails closed.
3. Once those pass, optional cached/mapped/mapped/cached30-second captures,
   all with the same four diagnostics. Keep focus and actual90Hz period checks,
   same Q/compression/geometry and selected first-use stereo age/cadence. Follow
   with untraced controls if comparing latency; observer cost blocks treating
   traced runs as promotion evidence. Do not infer benefit from component sums.
Pacer/publication traces diagnose observed supply/target/selection phases; they
do not measure photon latency or guarantee an alternative selection opportunity.
Coverage ends at the last exported checkpoint; an unexported terminal tail
remains unknown. A focus/refresh interruption stays recorded, not attributed.

Future quality seam, explicitly outside this milestone: capture acquired XR
swapchain pixels after defoveation with identical input view/FOV/rect/color,
scale/bias/cutout and a matched raw-RGBA reference. Current XR swapchain usage
lacks TRANSFER_SRC; legal opt-in readback needs a new capability/usage contract,
layout restoration and fence-observed readback before a successful-submission
identity is committed. Even that is app-submitted imagery, not displayed photons.
The initial requested foveal ROI is central10degrees radius; actual FOV and host
foveation mapping must be recorded. Gaze tracking is a later independent stage.
