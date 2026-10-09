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
