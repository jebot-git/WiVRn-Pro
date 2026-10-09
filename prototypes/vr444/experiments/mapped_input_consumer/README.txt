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

2026-10-09 coordinated matched timing
-------------------------------------
The completed Arc interval was 10:33:09.252370-10:40:41.151165 UTC (451.898 s);
UHD was 10:41:00.895161-10:48:38.372301 UTC (457.477 s). Both other research
teams held host execution throughout each batch; the desktop remained uncontrolled.
All 26 profiles x 9 arms x 31 repetitions = 7,254 measured invocations per device
were exact, as were the three warmups per arm/profile. Both completed audits bind
unchanged code, audit controls, binary, eight shaders, manifests and full corpora.
Each device also passed 234 deliberately omitted-right-eye controls.

The joint outer clock contains packet admission, decode, poison, image readiness,
and the first real stereo image consumer. Captured paired differences below are
median(candidate minus matching staged baseline), in milliseconds. Negative means
faster. All eight comparisons improved every captured pair's median; the p95
columns retain tail regressions. The same 12 pairs recur in every comparison.

Device  Matching staged -> direct   paired p50   paired p95   improved p95 / 12
Arc     RRF default                 -0.311205    -0.275055          10
Arc     RRF cached                  -0.302495    -0.270450           9
Arc     RRB32 default               -0.300315    -0.415325          12
Arc     RRB32 cached                -0.284695    -0.354345          12
UHD     RRF default                 -0.327525    -0.371740          12
UHD     RRF cached                  -0.326960    -0.362955          12
UHD     RRB32 default               -0.379735    -0.343755          12
UHD     RRB32 cached                -0.397690    -0.387110          12

Arc's five RRF p95 regressions across the two repeated comparison sets are retained;
the worst was +0.340200 ms. Arc default/cached labels actually select the same
memory type, so their differences do not demonstrate a cache-policy effect. UHD
uses distinct queried types, but its direct cached-versus-default p95 improved
only 2/12 RRF and 5/12 RRB pairs; no universal cached-allocation benefit is claimed.

Against the same-batch RRF CPU image-copy control, RRB32 direct-cached improved all
12 captured medians and p95s on each device. Its median paired joint difference was
-0.700110 ms on Arc and -0.471770 ms on UHD. RRF direct-cached improved all 12 on
Arc, but only 6/12 medians and 9/12 p95s on UHD (median paired p50 -0.004555 ms).
This makes RRB32 direct output the stronger private client candidate here. Its
unchanged captured wire growth versus global RRF is still +19.18% at Q1 and +5.13%
at Q8, and server/transport costs remain outside this comparison.

The reduction covers removed intermediate copies AND workspace clear/resize
zero-fill; all stereo RGB output bytes must still be populated. Repeated static
inputs and reuse, the three-submit poison path, host-visible consumer outputs,
and oracle/snapshot gaps limit translation to native workloads. Thirty-one
samples cannot establish a tail guarantee. No native or headset sub-15 claim is
made, and headset testing remains suspended.

timing-20261009.json retains both complete device reports, separate readiness and
joint per-pair comparisons, all 14 synthetic profiles, and postprocessing hashes.
The paired figure joint-captured.svg/png shows all 192 captured comparison points;
joint-captured.json binds its code, input receipt/manifest and image hashes. The
PNG was visually inspected. Seven Python tests pass, including rejection of
missing first-consumer evidence and malformed/incomplete joint statistics.
