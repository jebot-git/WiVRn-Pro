Mapped-input readiness through the first actual sampled-image consumer
=====================================================================

This host-only follow-up reuses the nine qualified mapped-input arms unchanged:
RRF CPU image copy, and RRF c4 / RRB32 staged or direct output with default or
required-cached input allocation. Wire format, decoded pixels, image formats,
CPU validation, allocation policy and lifetime checks are unchanged. It is an
additional private compute consumer, not native compositor/XR integration.

The whole outer clock starts before Engine.run and ends after a real stereo
SRGB sampling dispatch, observed fence and timestamp-query read. It therefore
includes the prior private poison/readiness submissions plus one consumer
submission. Readiness and first-consumer intervals are also reported separately;
the joint measured value is retained rather than adding independent percentiles.

Immediately after that clock ends, both first-consumer output buffers are copied
before verify() can reuse them. The independent oracle then checks every image
byte, alpha, finite sampled value and EOTF/OETF code bound. The first CPU-reference
warmup establishes independently verified same-device sampled float bits. All
saved first-consumer outputs must match that reference. Identical floating-point
samples are required only on that same physical device, never across drivers.

For every profile and arm, a separate correctness control retires prior writes,
poisons both consumer destinations, dispatches only the left eye, and saves those
outputs before the oracle. The left output must remain exact and the same stereo
comparison gate must reject the omitted right eye. This prevents later oracle
work or a stale prior right-eye output from concealing a missing consumer pass.
The original malformed/shape/late-decompressor failure checks remain present.

Synchronization review found no blocker: readiness has observed its fence and
query results before command/query reuse; both sampled images are shader-readable;
consumer writes become HOST_READ-visible before its observed fence; all vectors,
descriptors and images remain owned through the operations. The joint clock has
no deferred owned-buffer cleanup. Exact output snapshots/oracle costs occur after
the clock and affect inter-invocation gaps; only this new matched batch may be
compared directly. Desktop scheduling remains uncontrolled.

Build:
  bash prototypes/vr444/experiments/mapped_input_consumer/build.sh \
    validation/results/sub15-mapped-input-consumer/build

The shader root is BUILD/base. Build runs native/Android compilation (Android
compile-only), ASan/UBSan builds and eight SPIR-V Vulkan 1.1 validators. The probe
takes SHADERS_ROOT RRF_CORPUS RRB_CORPUS, plus --qualify RRF_GOLDENS or --reps N
and --device N. The latter indices select actual local Vulkan devices. No ADB,
headset, network, compositor or deployment operation is performed.

measure.py retains the full 26 matched profiles, 31 measured repetitions, three
warmups and rotated arm order. It requires explicitly coordinated research-team
idle time and hashes both entire corpora, manifests, executable, all eight shader
modules, recursively resolved C++ dependencies and the actual Python audit/run
controls before and after execution. Missing hardware/cache support fails an arm;
it cannot silently fall back or become performance evidence.

The first consumer is one nearest SRGB sample per pixel into a host-visible float
buffer. It does not model native draw geometry, warp, display scheduling or optical
response. Server preprocessing, RRF/RRB wire inflation and transport limits remain
outside this client experiment. Stage improvements cannot be added to separately
measured server/compositor medians or p95 values. Headset testing stays suspended.

2026-10-09 qualification
-----------------------
qualification-20261009.json binds the completed outputs, binaries, eight shader
modules and measured/validation sources. Both native Arc A770 and UHD 770 passed
170 profiles x 9 arms x 2 rounds = 3,060 exact valid invocations each, including
1,530 poisoned right-eye omission controls per device. Arc ASan/UBSan passed all
26 paired profiles x 9 arms x 4 rounds = 936 exact invocations and 234 omission
controls, with no diagnostics. All existing malformed/shape/late-decode gates
remain enabled. Four Python tests exercise 25 consumer/base-admission rejection
mutations. Host and Android builds plus eight SPIR-V validators pass.

Native correctness runs overlapped each other and other research work. Sanitizer
and qualification clocks are not comparative performance evidence. One premature
audit attempted to parse a still-growing native output and rejected truncated
JSON; completed zero-exit outputs subsequently passed the unchanged auditor.
Leak detection is disabled under ptrace; address/undefined checks remain enabled.
