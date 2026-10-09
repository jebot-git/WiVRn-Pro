Independent host-input native-render replay jobs (RLP1)

This adapter first repeats the full submitted_capture.py acquisition audit, then
joins one chosen capture to both independent host/{frame}-{eye}.rgba files and
unique encode-{eye}.jsonl records. Exact frame, eye, warp, input dimensions,
payload length and envelope CRC agree before a job is emitted. Those host files
are post-host-foveation codec inputs, not original application pixels. Neither
client-decoded pixels nor host .reference.rgba may substitute for them. Envelope
checks bind framing/identity; codec-body correctness remains the separate native
custom-evidence gate and is not inferred from a valid CRC.

  python3 validation/wivrn-codecs/integration/submitted_reference_job.py \
    --run-root /path/to/finalized/native-run \
    --build-receipt /path/to/build-receipt.json \
    --build-receipt-sha256 <explicit-sha256> --capture-id 1 \
    --out /path/to/NEW-job-directory

Optional --build-root and --run-hashes-sha256 have the same meanings as the
submitted-capture auditor. All source and candidate bytes are the same immutable
buffers used by that audit, not later re-reads or a rewritten audit report.
Output audit.json exposes job:{path,sha256,bytes} and output:{width,height,
vk_format}, the capture audit, independent source/envelope/event bindings, actual
shader bindings and complete input/source manifests. The job path is relative to
that receipt. Fresh output directories and exclusive writes prevent overwrite.

RLP1 is little-endian: six uint32 header values (0x31504c52,1,output width,height,
VkFormat,2), then each eye's six uint32 dimensions/foveation sizes/specialization
values, 112 native push-constant bytes, both uint32 foveation vectors, and the
independent packed RGBA input. The initial replay scope is opaque source, full
RGB rectangle, source SRGB view, the fixed linear/clamp test sampler, and both
alpha and explicit shader SRGB-conversion specializations zero. Axes/eye pixels
are bounded, each odd foveation vector has at most129 entries, and the job has a
40MiB cap. BGRA is an output attachment format, not a reinterpretation of source
RGBA. Both eyes are preflighted before the native replay may allocate GPU state.

The job reconstructs geometry from source-bound metadata. Nine significant
float digits preserve captured float32 push constants, but actual native VBO
and push-constant command bytes were not captured. Cross-compiler/AArch64 vertex
bit equivalence and actual raster equality remain unverified. The GPU team's
separately qualified replay must use the exact pinned native shader bytes and
same output format; a later comparison must retain these limitations.

This milestone builds input jobs only. reference_pixels_rendered=false,
quality_pass=false, displayed_pixels_verified=false, latency_eligible=false,
full_goal_qualified=false and promotion_eligible=false remain explicit. Exact
future submitted-image equality would still exclude runtime warp/filtering and
physical display pixels; visible-headset assessment is a separate requirement.

Validation:19 combined Python methods (11 capture +8 independent-job), including
rehashed wrong-right-eye/source identities, envelope CRC/geometry/flags, host
alpha/size, missing raw source without decoded-reference fallback, duplicate or
typed-invalid event IDs, unsupported replay state and source hash drift. Two
synthetic job formats are also accepted by the pinned host native --cpu-only
parser; malformed/truncated jobs reject. No device, GPU, sockets or latency
measurement is used here. Independent GPU-team source review found no blocker.

Reproduce Python checks:
  python3 -m unittest discover -s validation/wivrn-codecs/tests \
    -p 'test_submitted*.py' -v
Native parser replay:
  /path/to/pinned/submitted-image-replay JOB.rlp --cpu-only
Use the recorded native binary SHA in validation-20261009.json; its source/build
qualification belongs to the separately pinned submitted-image-replay branch.
