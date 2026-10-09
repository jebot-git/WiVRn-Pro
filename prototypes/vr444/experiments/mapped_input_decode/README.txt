Direct checked decompression into owned mapped GPU input
=======================================================

This experiment is host-only and changes no production codec or wire format.
Qualification is recorded below; no comparative performance result exists yet.

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
