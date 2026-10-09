Submitted-capture audit and candidate descriptor adapter

Run only after the native collector has finalized hashes.json. This read-only
adapter requires the pinned build-receipt SHA and validates actual helper and
renderer shader bytes against it, plus recorded binary/runtime digests. It does
not rebuild or independently attest the device. The actual recorder serial and
harness digest remain explicit recorded acquisition provenance.

  python3 validation/wivrn-codecs/integration/submitted_capture.py \
    --run-root /path/to/finalized/native-run \
    --build-receipt /path/to/build-receipt.json \
    --build-receipt-sha256 <explicit-sha256> \
    --out /path/to/NEW-audit-directory
Optional --build-root selects an archived native-source/shader root instead of
the receipt.source location. --run-hashes-sha256 additionally pins the exact run
manifest bytes; its observed digest is always retained. Every artifact is read
into one bounded immutable buffer and that exact buffer supplies both its hash
and parser/pixel transformation. Later uses reuse that buffer, not a different
path read. Paths cannot escape their declared root or use symlinks.

Requires restored/no-error cleanup, observed app PID, correctness summarypass,
exact enabled config readback, max1..8 paired captures, complete configured
capture1..N population, no unbound/incomplete files, and both final pair markers
and per-eye raw/metadata. Typed integer identities reject Python bool aliases.
Stereo frame/warp/generation/submit/geometry must agree. Each pair joins exactly
one presentation using frame, warp, end_frame_return_xr_ns and predicted target.
The later presentation.submit_xr_ns includes capture serialization/disk work and
is deliberately not substituted for the actual successful XR return timestamp.
Sampling is checked against the corrected absolute frame%stride==0 grid.

Every native raw format/stride/bounds/channel flag is checked. RGBA is unchanged;
BGRA becomes RGBA solely through reversible [2,1,0,3] channel permutation, with
input and output hashes retained. No interpolation, gamma operation or lossy
conversion occurs. Actual unclamped unwarped FOV and foveation mapping are
validated. Projection JSON and a conservative central10degree pixel-footprint
mask are emitted with independent hashes, fixed-center scope and geometry IDs.

The required renderer receipt entries bind raw optimized SPIR-V and the actual
ASCII embedded uint32 literal. The adapter parses full bounded literal grammar
and independently reconstructs little-endian bytes, requiring exact equality.
Both native shader names and the actual fixed test-decoder sampler/SRGB view
contract are checked. Device-specific rasterization and runtime composition are
not inferred from these source and byte bindings.

Output audit.json contains candidate descriptors, source/read manifests and
explicit independent_reference=None, quality_pass=false, latency_eligible=false,
full_goal_qualified=false. It also copies the pinned build receipt verbatim.
No submitted-reference comparison has happened. Missing independent same-render
raw reference and visible-headset assessment remain explicit requirements.
The emitted descriptors cannot silently become a decoded/staging reference.

Current validation:11 adversarial test methods, including29 rehashed metadata
mutations, wrong-right-eye joins, boolean IDs, absent final marker, corrupt source
boundary, malformed FOV/formats, raw/helper hash drift, forged embedded shader
literal, duplicate JSON, unbound extra artifact, symlink and output-overwrite
rejection. Independent codec-team read-only review found no capture-join or
false-positive quality claim blocker. The real sealed build receipt's two
raw/embedded renderer shader pairs also pass byte-equivalence audit; no actual
submitted capture or device operation was executed for this milestone.

Reproduce:
  python3 -m unittest discover -s validation/wivrn-codecs/tests \
    -p test_submitted_capture.py -v
