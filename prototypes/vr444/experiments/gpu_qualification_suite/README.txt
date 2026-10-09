GPU research handoff and offline qualification suite
===================================================

This directory collects the GPU team's separately published approaches. It is
not a native-client integration, a headset test, or evidence that sub-15 ms has
been achieved. The committed catalog pins every approach to an immutable commit
and names its qualification and measurement receipts. Read those receipts and
their adjacent notes for the actual hardware, input scope and rejected arms.

2026-10-09 fresh result: all declared host-level stages passed from a new build
directory, 10:16:10--10:19:48 UTC. Both Arc A770 and UHD 770 completed 170 profiles
x 9 arms x 2 rounds = 3,060 exact valid invocations each. The CPU sanitizer matrix
passed all 838 cases, two reuse rounds and 8,382 rejection checks. Android native
compilation and all eight SPIR-V validators passed. Source and corpus snapshots
were unchanged; qualification-20261009.json records the full execution receipt,
source/binary/log hashes, individual audit counts and explicit historical scope.
These clocks are execution duration, not comparative performance evidence.

Two distinct kinds of evidence
-----------------------------
history.py reads the pinned Git objects and verifies the source hashes explicitly
recorded in each receipt against that same historical commit. It does not compare
old results with today's changed helper files. Untracked binaries, raw captures,
and old execution logs are listed as external artifact hashes NOT reverified by
the archive check. Source coverage varies between early explicit file lists and
later recursively resolved dependencies; a historical source binding is not a
new execution or a claim that every build dependency was archived.

run.py separately executes current-worktree checks. Its audits level runs all 13
declared Python audit groups, including negative evidence-admission tests. CPU
level also builds the final mapped-input probe and CPU failure matrix from source,
cross-compiles Android without executing it, validates eight Vulkan 1.1 SPIR-V
modules, and runs the 838-case / 8,382-rejection CPU matrix under ASan and UBSan.
Host level additionally runs the complete 170-profile, nine-arm final mapped-input
matrix on each explicitly selected LOCAL Vulkan device and audits every result.
Earlier palette encoders, image-copy extensions and timeline probes remain
historical evidence in this aggregate; their GPU executions are not rerun by the
final mapped-input matrix. Their individual build scripts remain available.

No skipped or empty Python suite counts as a pass. Missing capabilities, failed
builds, changed source/corpus hashes, omitted stages, incomplete matrices and
non-hardware Vulkan fallbacks fail the requested checks. The report names every
executed stage. Audits/CPU levels never claim that the host checks ran. All levels
retain promotion_eligible=false and performance_qualification=false. Correctness
probe clocks are deliberately not used as comparative performance results.

Run current audit checks:
  python3 prototypes/vr444/experiments/gpu_qualification_suite/run.py \
    validation/results/gpu-suite-audits

Run the declared fresh host checks (the two corpus exports are local evidence):
  python3 prototypes/vr444/experiments/gpu_qualification_suite/run.py \
    validation/results/gpu-suite-host --level host \
    --rrf-corpus /tmp/sub15-rgb-rowfilter-gpu-corpus \
    --rrb-corpus /tmp/sub15-rgb-rowblocks-gpu-corpus \
    --host-device 0 --host-device 1 \
    --icd /usr/share/vulkan/icd.d/intel_icd.x86_64.json

The output directory must be new. Keep output under ignored validation/results
so generated receipts do not become source inputs during the drift check. Native
build requirements are clang/clang++, glslc, Vulkan headers/loader and Python 3;
Android and spirv-val use ANDROID_NDK_HOME (the individual build script documents
its local default). An unavailable tool or corpus produces a failure, not a
substitute performance or device result. ASan leak detection is disabled because
this environment executes under ptrace; address and undefined-behavior checks
remain enabled. No command here controls ADB, XR, deployment or a network test.

Interpretation boundaries
-------------------------
Host stage improvements cannot be added to server-worker medians or compositor
quantiles: those scopes overlap and percentile arithmetic does not compose.
Report comparisons within their matched batch and preserve individual tail
regressions, all synthetic/noise cases and the independent transport size caps.
RRF and RRB preserve decoded RGB444 quality, but their current Q8 wire inflation
and protocol integration costs still matter. GPU samples are compared with the
same physical device's independently verified SRGB reference, not float equality
across drivers. A host extension query never qualifies a headset capability.

The final image prototypes own private resources and intentionally count image
poison, synchronization and queue overhead. Native integration still needs actual
free-slot retirement, preservation of the currently displayed stereo pair on
failure, exact negotiated formats, and complete packet-to-consumer accounting.
The compositor team's independent receipt/target/identity analyses constrain the
latency objective; a faster private image stage alone does not establish that
objective. Headset testing remains suspended under the research instruction.
