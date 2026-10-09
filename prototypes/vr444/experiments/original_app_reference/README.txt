Original application -> submitted image reference
=================================================

This independent adapter leaves the existing post-foveation replay, shader,
renderer executable, device runner and native acquisition unchanged. It prepares
RLP1 using literal app-source/eye{0,1}.source.rgba from the exact successful
application target/image joined by source_mapping.audit. It executes no GPU,
ADB, OpenXR or networking operation.

The source-map auditor is recomputed, not imported as a PASS assertion. It binds
actual completed producer UBOs, rect/flip, image index and original app source
bytes via the runtime-converted host target, app delivery/latch, frame, warp and
eye. Both captured native eyes must have the same full source/output extent and
bit-exact float32 input/render/source FOVs. A source rectangle/row permutation
is the only pixel transformation: no resize, resampling, gamma or codec output
is used as reference. Current upstream mapping supports full positive rects.

The reference replaces the variable foveation partitions with one full-resolution
segment per axis. Its binary32 vertex endpoints must be exactly -1 and +1. This
is an independently specified full-screen reference, not the captured candidate
VBO. The qualified replay uses the exact selected native SPIR-V, SRGB input and
output views, linear normalized clamp sampler, and no alpha specialization.
This first adapter requires unity color scale, zero bias, inactive cutout, no
alpha image and opaque original RGBA. Submitted pose is retained as identity
metadata; the private defoveation shader has no pose transform. Runtime timewarp
and physical/optical display output remain outside the saved image comparison.

The offline comparator recomputes the job and source-map evidence, requires the
archived job/report to match, re-audits the complete original actual-device
acquisition and its right-eye omission control, and recomputes native candidates
and central10 masks. It independently checks:

  * uncompressed reference output versus literal original code values;
  * actual submitted output versus uncompressed reference;
  * actual submitted output versus literal original code values.

A one-code difference is a failure. Equality between two identically changed
outputs cannot hide a reference filtering/SRGB difference. Alpha is assessed
separately and must stay opaque. There is no guessed gamma tolerance, silent
post-foveation fallback or synthetic-evidence qualification path.

The per-frame report does not qualify the research route: the unchanged target
still requires two native acquisitions of each of chroma-grid, rgb-ramp and
fine-detail, at least three distinct complete stereo frames at both required
stages, same candidate/build and all immutable evidence. Human observations stay
separate. The prior observation “Grid visible, some banding artifacts between
pixels” remains a failed visible-artifact observation, with origin unresolved.

Preparation (host only; explicit immutable pins required):

  python3 validation/wivrn-codecs/integration/original_reference_job.py \
    --run-root RUN --run-sha RUN_HASHES_SHA \
    --build-receipt BUILD --build-sha BUILD_SHA \
    --application-build-receipt APP_BUILD --application-build-sha APP_SHA \
    --capture-id 1 --out FRESH_JOB_DIRECTORY

Use the unchanged submitted_image_replay/capture.py prepare command with the new
job audit.json and its SHA, the same native build receipt, expected physical
serial and actual native campaign identity bridge. It hash-checks the existing
qualified host/Android binaries and tests RLP1 grammar with the host binary's
--cpu-only mode. Root alone executes the resulting unchanged bounded runner.
Each replay render and omission render retains its existing 60-second device
watchdog; this is correctness evidence, never a latency timing campaign.

Offline comparison after root acquisition:

  python3 validation/wivrn-codecs/integration/original_reference_compare.py \
    --run-root RUN --run-sha RUN_HASHES_SHA \
    --build-receipt BUILD --build-sha BUILD_SHA \
    --application-build-receipt APP_BUILD --application-build-sha APP_SHA \
    --capture-id 1 --job-receipt JOB/audit.json --job-sha JOB_SHA \
    --replay-directory RAW_DEVICE_CAPTURE --replay-plan PLAN \
    --replay-plan-sha PLAN_SHA --runner PINNED_EXISTING_RUNNER \
    --out FRESH_ABSOLUTE_COMPARISON_DIRECTORY

The adapter never fabricates missing actual source-map evidence. Earlier v2
captures predate the required source-map producer and schema3 clock join, so
those captures cannot instantiate this original-app reference retroactively.

The independently reviewed original_source_pixels helper defines the comparison
mask as the union of the entire submitted central10 cone and the proven-permuted
original central10 cone. Asymmetric Y-flip geometry never silently shrinks either
mask. It separately checks captured post-host-foveation candidate codes on the
required unit footprints plus one encoded-cell halo. Nonunit footprints or code
mismatches remain explicit failures, while safe reference construction remains
available for diagnosis. Post-foveation bytes only assess this candidate source
stage; they never populate or alter the original reference.

The exact report field finite_original_source_to_submitted_exact requires both
the source-stage footprint result and all three actual output comparisons. No
claim of captured VBO bytes or floating UV equivalence is needed: the independent
identity reference's actual original-code equality is measured explicitly.
