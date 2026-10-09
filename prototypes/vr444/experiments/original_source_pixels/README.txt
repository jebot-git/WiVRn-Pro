Literal original application pixel proof

This isolated helper supplies a strict per-eye pixel operation to the original
application replay adapter. It performs no device, graphics or file acquisition.
The caller must recompute and bind source_mapping, application and actual native
capture audits, complete stereo/run/build/shader identity, and the physical replay
collector. It must never accept a caller-supplied proof in place of recomputation.

API: validation/wivrn-codecs/integration/original_source_pixels.py
  prepare(record, eye, source_rgba, native_meta, app_view, *, encoded_rgba)
  compare(prepared, actual_native_raw, actual_replay_raw)

The original reference is the literal application RGBA image with only the actual
full-rectangle UBO row permutation. There is no resizing or color conversion.
Original source/output shape, actual ratios, FOV float bits, unchanged render/input
pose, opaque source, source image identity, SRGB view and linear sampler, unity
color and disabled cutout are checked. RGBA/BGRA output channel order is explicit.
The mask is the UNION of the entire submitted central 10-degree cone and the
proven-permuted original-source cone. Asymmetric flipped masks need not be equal;
an intersection is never used. Actual UBO footprints and one encoded-cell adjacent
halo report required original texels, nonunit cells and exact source-stage errors.
Post-host-foveation bytes are used only as candidate data for that stage.

The comparator requires THREE zero-tolerance RGB comparisons: native-to-original,
replay-to-original, native-to-replay. Alpha stays separate and must remain opaque.
A shared wrong native/reference result therefore cannot qualify. Nonunit/source
code failures remain explicit false proofs, permitting a diagnostic reference for
a failing candidate. Source-stage failure prevents finite combined success even
if the final captured output happens to equal the original pattern.

Eight test groups pass, including 128 seeded independent scalar rectangle/neighbor
references, both flips and RGBA/BGRA formats, asymmetric cones, source-halo-only
errors, same wrong replay/native code, RGB/alpha changes, invalid geometry/color/
identity/sampler and missing or changed bytes/masks. qualification.json pins the
actual project modules loaded by this test and their unchanged hashes; tests.txt
is the fresh run. These are finite synthetic tests, not collected live evidence.
GPU agent reviewed the pure helper with no blocker; its typed identity suggestion
was applied. Integration into the replay adapter has a separate qualification.

The one-cell halo uses the source-bound native mesh/LINEAR adjacency. Actual native
VBO/push bytes are not captured or inferred equivalent. Direct actual output code
equality is required independently, including the identity replay's own equality
to the original. No gamma/filtering roundtrip is presumed exact. Aggregate fixture
coverage, required post-foveation replay, actual visible assessment, runtime
reprojection and optical pixels remain separate. full_goal_qualified and display
qualification are always false here.
