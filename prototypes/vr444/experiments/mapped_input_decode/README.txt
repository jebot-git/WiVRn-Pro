Direct checked decompression into owned mapped GPU input
=======================================================

This experiment is host-only and changes no production codec or wire format.
Qualification and the matched two-device comparison are recorded below.

Hypothesis: remove the intermediate RGB/residual workspace-to-input memcpy by
writing the bounded LZ4 output directly into a retired, coherent mapped input
allocation. Raw blocks copy directly from their immutable packet. All six bytes
per stereo pixel still become GPU-visible; no claim of zero bus traffic follows.
This also bypasses the workspace clear/resize zero-fill. Any observed difference
belongs to the complete direct-output path, not to memcpy alone. Temporary index
destruction is included before the whole outer clock stops.

Memory selection is a separate variable. Earlier actual allocations chose flags
14 (HOST_VISIBLE|HOST_COHERENT|HOST_CACHED) on Arc, and flags 7
(DEVICE_LOCAL|HOST_VISIBLE|HOST_COHERENT) on UHD. LZ4 reads previously written
output for matches, so coherent but uncached output may be a poor CPU target.
The new comparison must distinguish copy removal from requesting a cached type,
and must fail closed if an actual buffer has no compatible cached coherent type.
Physical-device inventory alone does not prove buffer memory-type compatibility.

Pair is a noncopyable, single-use synchronous lease over two immutable wire views
and two distinct output spans. Its constructor checks capacities, all same-eye and
cross-eye aliases, both complete CRC/grammar inspections, and both dimensions.
No output is written during admission. Decoder invocation consumes the lease;
only two complete decompressions and explicit packed-word tail padding establish
readiness. Every failed or repeated invocation clears that readiness state.

A decoder error after admission may have written a partial input buffer. It must
not lead to a readiness submission. A new pair must perform full admission and
overwrite every byte before recovery. The private image harness poisons its own
images and observes the fence before these CPU writes; no displayed image exists
in this prototype. Production integration would need a retired free slot and
must preserve any currently displayed/leased resource. These lifetime obligations
are not satisfied merely by a successful host benchmark.

The existing CPU workspace path remains the matched control. Both paths must
count CRC, strict compressed grammar, all decompression, metadata, actual input
population, image poison, queue recording, fence waits and readiness overhead.
An independent byte/color oracle remains outside the outer clock. All source
packets, image bytes, stereo order and RRF/RRB prediction rules remain unchanged.

Extension alternative considered: VK_EXT_external_memory_host can import an
existing host allocation. Both host GPUs advertised it and reported importable
STORAGE_BUFFER|TRANSFER_SRC support for HOST_ALLOCATION, with 4096-byte pointer
and allocation-size alignment in the read-only capabilities query. Import of an
actual pointer was not attempted. The pointer-specific compatible memory types
would still require a query, and ownership must outlive the Vulkan memory object.
This extension cannot by itself remove a copy into a different decoder buffer.
The existing mapped-input allocation permits the direct-output experiment using
core Vulkan, so external pointer import is not required for this candidate.

Primary references consulted for that alternative (not measured speedup claims):
https://docs.vulkan.org/refpages/latest/refpages/source/VK_EXT_external_memory_host.html
https://docs.vulkan.org/refpages/latest/refpages/source/vkGetMemoryHostPointerPropertiesEXT.html
https://docs.vulkan.org/refpages/latest/refpages/source/VkPhysicalDeviceExternalMemoryHostPropertiesEXT.html
https://docs.vulkan.org/spec/latest/chapters/memory.html

No desktop capability is assumed on the headset. No headset, XR, network or
optical latency operation belongs to this experiment.

Nine matched arms
-----------------
RRF CPU SIMD image copy is the independent byte/color reference. RRF c4 GPU and
RRB32 GPU each have four arms: staged/default memory, direct/default memory,
staged/required-cached memory, and direct/required-cached memory. Every requested
cached input must select an actually compatible coherent cached type; absence
fails the arm and does not silently fall back. Buffer/image allocations persist.
Staged RGB workspaces reuse capacity; direct checked indices are constructed and
destroyed inside each full outer clock.

The actual Arc inputs use type 1 / flags 14 in all arms, so its cached labels are
duplicate memory-selection controls. UHD default inputs use type 1 / flags 7,
and required-cached inputs use type 2 / flags 15. The independent audit rejects
staged/direct arms with different actual memory properties within a comparison.
All source pixels and wire bytes are unchanged; RRB32 source selection follows
the preceding full four-partition timing branch, not removal of unfavorable
source profiles. All 26 matched profiles remain present.

The standalone oracle now avoids constructing temporary error strings for
successful literal checks. It retains every image-byte, sampled-bit, finiteness,
alpha and EOTF/OETF roundtrip check. This removes probe overhead, particularly
under sanitizers, and changes gaps/cache/frequency behavior between invocations.
Only a new within-batch comparison is valid; subtracting older branch timings
would confound that common instrumentation change with the new input path.

2026-10-09 qualification
-----------------------
qualification-20261009.json binds all sources, binaries, shaders and corpus hashes.
CPU ASan/UBSan: 838 RRF/RRB cases, two reuse rounds, 8,382 rejections, independent
residual-byte comparisons, rounded-tail canaries, same-eye/cross-eye aliases,
failed-token reuse and late right-eye failure/recovery. No Vulkan initialization.
Five synthetic memory-selection controls reject missing query data, incompatible
type masks, missing host visibility, absent required caching and invalid counts.

Final native Arc: 170 profiles x 9 arms x 2 rounds = 3,060 exact valid invocations.
Final native UHD and GPU ASan/UBSan each: 26 profiles x 9 arms x 4 rounds = 936.
Every arm rejects malformed and wrong-shaped right eyes before writes/submits.
Direct compressed cases also inject a decoder failure after poison and verify
that no readiness submission occurs; subsequent valid reuse passes the oracle.
Temporary index cleanup was moved inside the complete outer clock before these
final runs. The independent compositor-team source review found no blocker.

Six audit tests include 65 rejection mutations. Host and Android compilation,
eight SPIR-V Vulkan 1.1 validators and core-only Shader capabilities pass.
ASan/UBSan emitted no diagnostics; leak detection is disabled under ptrace.
Correctness clocks may be contended and are not performance evidence.

Build:
  bash prototypes/vr444/experiments/mapped_input_decode/build.sh \
    validation/results/sub15-mapped-input-decode/build

The host probe takes SHADERS_ROOT RRF_CORPUS RRB_CORPUS, with --qualify RRF_GOLDENS
for the generated/boundary/maximum matrix or --reps N --device N for all matched
captured/synthetic profiles. measure.py requires explicit research-team idle
coordination and binds both complete source corpora, both manifests, all eight
SPIR-V modules, executable and 22 recursively resolved local source dependencies.

2026-10-09 coordinated comparison
---------------------------------
timing-20261009.json retains all 26 profiles, nine arms, per-case p50/p95/max and
stage summaries for both physical devices. Each device completed 7,254 measured
invocations (31 repetitions) plus 702 warmups. All exact image/color checks pass.
Both research teams explicitly held builds/tests/measurements; desktop workload
remained uncontrolled. The actual process intervals were:
  Arc: 09:54:21.204192--10:01:21.522463 UTC, 420.317962 seconds.
  UHD: 10:02:00.314918--10:09:04.299709 UTC, 423.984479 seconds.
Both entire corpora, manifests, executable, eight shader modules and all 22
measured source dependencies were unchanged before/after each run and identical
between device batches. Source edits during the window were confined to a new,
unrelated suite directory, outside these dependencies; no additional research
build, test, benchmark or analysis ran.

Captured-pair whole-readiness results: median of the 12 per-pair differences,
candidate DIRECT minus matched STAGED, in milliseconds. Negative is faster.
The last column is the number of captured pairs whose p95 improved (of 12).

  GPU  format  actual-memory selection   paired p50 delta  paired p95 delta  p95
  Arc  RRF     default                   -0.324525         -0.359235         11
  Arc  RRF     cached                    -0.270860         -0.316665         10
  Arc  RRB32   default                   -0.293470         -0.297620         11
  Arc  RRB32   cached                    -0.280295         -0.206365         12
  UHD  RRF     default                   -0.318030         -0.279020         12
  UHD  RRF     cached                    -0.337430         -0.344830         12
  UHD  RRB32   default                   -0.344665         -0.359460         12
  UHD  RRB32   cached                    -0.354975         -0.445680         12

All eight comparisons improve all 12 captured p50 values. Arc has four p95
regressions across the four sets of 12 paired comparisons; they remain in the
receipt, including a +0.440880 ms RRF cached-arm case. These sets are overlapping
controls, not 48 independent captured pairs. Thirty-one samples do not establish
a production tail bound. Direct output is promising for this isolated stage,
while its cleared-workspace/temporary-index differences prevent attributing the
whole gain solely to memcpy removal.

The CPU reference comparison is device-dependent. RRF direct/cached improves all
12 Arc p50/p95 pairs, but on UHD only 6/12 p50 and 7/12 p95 pairs improve, with
median paired p50 delta -0.000450 ms. RRB32 direct/cached improves all 12 p50/p95
pairs on both hosts: median paired p50 delta -0.666405 ms on Arc and -0.439975 ms
on UHD. This does not include RRB's server work or wire inflation: the unchanged
RRB32 captured wire is +19.18% at Q1 and +5.13% at Q8 versus whole-frame RRF.

Cached/default Arc allocations are identical, so their differences are controls
for run variation, not cache-selection gains. UHD's actual cached selection has
a much smaller/inconsistent median benefit than direct output; no universal
memory-policy promotion follows. All six stereo RGB bytes per pixel are still
written to GPU-visible memory, and the private two-submit poisoned readiness
path is not native packet-to-render/optical latency. No headset test or sub-15 ms
gate was evaluated. Nine Python audit/summary tests now pass, including omission,
changed-source and paired-quantile interpretation negative cases.

Regenerate the numeric receipt from the audited reports with summarize.py. That
postprocessor requires the complete captured/synthetic matrix, matched code/input
identity and successful audit conditions; its own source hashes are recorded.
