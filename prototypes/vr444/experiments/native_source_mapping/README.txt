Actual host source mapping, quality-only diagnostic
=================================================

Default off. WIVRN_CODEC_SOURCE_MAPPING_TRACE is an absolute new JSONL path,
intended as <run>/host/source-mapping.jsonl. Native activation requires explicit
rrb32-q1, Q1, compression none, quality capture1, absolute source-trace/evidence
paths, and frame stride1. Root must scrub inherited activation for all controls.
No new device, GPU wait, queue, listener, transport or codec mode is added.

Capture samples admitted compositor frames divisible by90, matching existing
host source artifacts. It records actual mapped UBO edges after foveate(),
including cached update hits, plus compact parameters, actual source subrect,
flip, source FOV float bits (left/right/up/down), native image/view/swapchain
identities, array/image indices and creation shape. Handles identify objects
only within the server process; they are not cross-process app VkImage IDs.
View format is source-derived from pinned Monado view creation, not queried.

Each private record is retained across layer cleanup and appended only after
the matching actual producer timeline value completes. A scope guard poisons
the whole receipt for any exceptional/timeout exit on an admitted GPU path,
including unsampled frames. Capacity128, axes4096, per-eye4MP, exactly two
opaque RGBA8_SRGB source images, positive subrects and non-VIEW projection
are the initial supported capture contract. Overflow and unsupported sampled
routes fail closed; no missing record is silently labeled complete. The final
adapter additionally requires every sampled successful encoded stereo frame.

source-clock-qualification.json in ../quality_stimuli binds the schema3 fixture
requiring XR_KHR_convert_timespec_time. Original XrTime and actual checked
CLOCK_MONOTONIC sec/nsec/ns remain separate fields. LOCAL is required for this
source route. Historical VIEW and schema1/2 captures are unchanged.

Native qualification:20 ASan/UBSan recorder checks; full compositor.cpp and
foveation.cpp syntax with WIVRN_CODEC_TESTS ON and OFF. No GPU/OpenXR/device
execution. Native source/build/asset integration and final acquisition belong
to the root coordinator. The independent comparison adapter and tests follow
as a separate commit; this component alone is not a source or quality pass.

Harness and independent audit
-----------------------------
source_mapping_control.validate(args, source=None) performs offline admission.
Required opt-in args: source_mapping=True, mode=custom-rrb32, rrb_source=q1,
q=1, compression=none, source_trace=True, quality_capture=True, and the already
validated explicit LOCAL quality_application_plan plus its SHA. Four additional
fields are required only when enabled:
  source_mapping_build_receipt
  source_mapping_build_receipt_sha256
  source_mapping_app_build_receipt
  source_mapping_app_build_receipt_sha256
validate returns the five native source paths for native provenance binding.
The source argument verifies the actual native files against the reviewed
current overlay. Receipt bytes/source hashes are checked before live work.

configure(args, env, output) scrubs inherited mapping activation, sets the
absolute sidecar path plus explicit QUALITY_CAPTURE=1 and FRAME_STRIDE=1, and
returns provenance additions. Other existing Q1/app/source gates remain root's
responsibility. Fixed frame stride1 is not a new user CLI field.
evaluate(args, output) runs before final summary/hashes: checks bound receipt
and native binary/source provenance, complete sidecar/clock/source-latch joins
and sampled encoded stereo coverage, then returns hashes of the actual reads.
Its status is provisional acquisition admission; it never claims a final run
manifest, original-source comparison, quality or latency qualification.

After final hashes.json and summary.json are sealed, run:
  python3 validation/wivrn-codecs/integration/source_mapping.py \
    --run-root RUN --run-sha SHA_OF_RUN_HASHES_JSON \
    --build-receipt NATIVE_RECEIPT --build-sha NATIVE_RECEIPT_SHA \
    --application-build-receipt APP_BUILD_RECEIPT \
    --application-build-sha APP_BUILD_RECEIPT_SHA --out NEW_REPORT_JSON
Optional --build-root selects an archived native artifact root. The separate
external Monado source path in its pinned receipt must remain available.
Native receipt foveation_shaders.foveation_rgb.comp binds raw SPIR-V and the
embedded literal; the auditor recomputes equivalence. monado_source_view_contract
and external_artifacts_sha256 bind actual comp_swapchain.c view creation.

Formal source mapping replays the existing submitted-capture and original app
source/readback auditors, binds actual plan/snapshot/argv/build provenance, then
matches the runtime-converted target to unique delivered+predicted app identity,
its compositor latch and actual mapping frame/warp. App sequence is explicitly
not Monado app_frame_id. Source FOV, released image index, full positive source
rectangle, both image shapes and sample/array/mip/face counts are checked.
All successful sampled encoded stereo eyes and host source files must appear;
whole stereo envelopes remain capped at262144bytes.

It independently expands compact ratios and checks every actual UBO edge.
The central10-degree mask uses original application projection rays and includes
all texel footprints touching that cone. Only actual consumed1x1 footprints can
qualify exact original source codes; constant/repeated patterns cannot hide
averaging. RGB is compared at0code tolerance, alpha separately. A one-encoded-
cell halo reports nearby nonunit footprints but does not certify native raster
interpolation. No SRGB round-trip is assumed exact: actual post-foveation bytes
must match the independently generated original app texels.

protected_source_stage_exact is a finite source-stage result. Full quality,
latency, displayed pixels and downstream filter/raster equivalence remain false.
The existing scalar codec and submitted-reference audits remain separate. This
adapter does not reinterpret old VIEW/schema1/2 captures as clock-joined LOCAL.
Audits trust the explicit pinned upstream acquisition/build receipts; they do
not authenticate a hostile host or reconstruct a build/runtime environment.

Adapter qualification:11 Python test groups, including a complete new formal
path with separately mocked upstream submitted context; rehashed semantic
mutations (source/frame/warp/target/FOV/image/receipt/missing-eye), one-code RGB
error, independent alpha error, reversed Y, cached mapping, malformed bounds,
constant-source averaging, actual native provenance names, and preflight
negative controls. Eight existing quality_application tests pass unchanged.
GPU peer review found no remaining material blocker; no live source-map receipt
or original-source-stage success exists until the coordinator runs v3.
