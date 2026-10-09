Host latency experiment: mapped bulk packing and span input

This is an isolated prototype, not an installed/live-codec change. The baseline/
directory contains copied VR444 codec and GPU runner files. codec.cpp/hpp are
unchanged reference implementations; vulkan_runner.cpp/hpp add timing fields and
an alternative encode_packed method. Both paths retain the same compute shaders,
Vulkan device, coherent mapped allocations, barriers, dispatch, submit and fence
wait. No borrowed production device or asynchronous GPU compaction is implemented.

Selected bottleneck: GpuSession::encode previously allocates/zeroes a Frame's
expanded slots and copies all 320 words per tile back to it, after which pack()
serializes only the used words via bytewise push_back. The live producer also
constructs a separate CPU RGBA vector before the runner copies it into its mapped
input. pack_mapped.hpp instead validates lengths/maps, sizes a reusable wire vector
once, and copies only used tile words directly from mapped output after the fence.
The little-endian fast path uses bounded memcpy; other hosts serialize words
explicitly. VR44 versions1/2 and exact bytes are unchanged. The input-span variant
also skips the intermediate CPU vector, while retaining the GPU input upload.

Variants (same shader, rotate their order on every repetition):
0. baseline-vector-expanded-bytepack: extra input vector, expanded Frame, old pack.
1. vector-expanded-bulkpack: keep Frame and input copies; reusable bulk pack only.
2. vector-mapped-bulkpack: keep input vector; pack mapped GPU slots directly.
3. span-mapped-bulkpack: skip intermediate input vector and expanded output Frame.

encode_packed returns GpuResult.packed borrowing the session's reusable vector.
Consume or copy it before the next encode_packed call and before session teardown.
Each session belongs to one producer; it is not concurrently callable. Fence and
shader-to-host barriers remain mandatory. pack_mapped is for internally generated
GPU/CPU codec slots; it validates structural bounds, not all coefficient grammar.
Use the ordinary parser/decoder as the external-data oracle. Inputs must not alias
the output vector allocation. Output remains bounded to16 MiB.

Build and CPU/sanitizer checks (from workspace):
  bash prototypes/vr444/experiments/latency_host/build.sh OUTPUT/build
  OUTPUT/build/probe --self-test
  OUTPUT/build/probe-sanitizer --self-test
Arc benchmark (coordinate against other GPU runs):
  OUTPUT/build/probe --benchmark SHADERS CORPUS OUTPUT/benchmark.json
  LATENCY_HOST_REVERSE=1 OUTPUT/build/probe --benchmark SHADERS CORPUS OUTPUT/benchmark-reversed.json
  python3 prototypes/vr444/experiments/latency_host/summarize.py OUTPUT
The historical first round predates cpu_outside_submit_ms; its source and binary
are preserved in results0.13/host/source-initial and build/probe-initial. Repeating
now produces that extra timing field in both rounds without changing codec output.

CORPUS contains eight 576x640 RGBA captures with source hashes in corpus-manifest.
Six full-size synthetic fixtures and two partial 17x19 per-tile-Q cases are added
by the probe. Every emitted wire payload must equal both original GPU-pack output
and the CPU encoder's canonical bytes; CPU decode is checked too. Lossless uniform
Q1 cases additionally compare to the original RGBA bytes. Five warmups and31 timed
iterations per variant are used. Each fixture has its own persistent session.

Timing limits: starts with cached CPU input and excludes the original producer
GPU readback/fence, LZ4 wrapping, network and client. Host wall time outside submit
subtracts the measured queue-submit/fence-wait interval; it is not CPU cycle time
and can include scheduling/preemption. Total time ends before local destructor
work, so baseline vector reclamation is omitted. First-use allocation/initial
pipeline creation is excluded. The captured inputs are already host-foveated,
not a representative VR corpus. Source/display/optical quality is not measured.
