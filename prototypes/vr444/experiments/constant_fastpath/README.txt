Expanded-slot constant fast path, opt-in decoder experiment

Build from repository root:
  bash prototypes/vr444/experiments/constant_fastpath/build.sh OUTPUT
Run Linux or Android binary with identical arguments:
  OUTPUT/probe OUTPUT/base OUTPUT/fast CORPUS 0 21
  OUTPUT/probe-android BASE_DIR FAST_DIR CORPUS_DIR 0 21
The prior compact experiment corpus contains the 38 legacy .payload files needed:
  validation/results/codec-viability-0.8.0/compact/corpus
The runner ignores VRI2 files; this experiment uses only the existing live VR444
wire format and expanded 320-word input slots. No compact-format parser is used.
The parent agent operates ADB; this directory never controls devices.

Algorithm
A DC-only tile stores three channels, each with 11 DC bits then sixteen zero AC
group flags, totaling 81 bits in three words. The shortcut checks length==3 and
ALL bits outside the three DC fields are zero, including padding. Length alone
is unsafe: a repeated nonzero AC group fits in 88 bits, still three words.
Every invocation reads the same immutable three slot words and length. A matched
workgroup reconstructs integer YCoCg DC directly, clamps RGB, writes alpha255,
clips partial tile output, and returns. No shared memory or barrier is used in
that branch. All invocations in the workgroup take the same branch. Unmatched
workgroups use the unchanged scalar lane-zero parser and cooperative inverse,
including every original shared-memory barrier. Workgroups never communicate.
The existing CPU validator remains required before GPU dispatch. The shader is
not a replacement for packet grammar, DC/AC bound or allocation validation.
Quantizers do not affect DC; the shortcut works with uniform and per-tile Q.

Harness
Both decoders use the same copied GpuSession runner, with one deliberate change:
every output buffer is poisoned with 0x5a before every decode in BOTH paths. Both
paths therefore include equal output write/readback costs and missing stores
cannot pass because a previous correct image remains in memory. Their input
layout, memory allocation, CPU validation, descriptors, dispatch count, shader
compiler flags and timestamp ranges are identical. Reference encode shader is
identical (not dispatched during decode). Sessions are cached by dimensions.
Three warmups plus configurable measured repetitions are run for each case,
alternating which decoder runs first. Every call must match the CPU decoder for
all four stored channels; errors include path/case/repetition/first differing
pixel. Timing samples are sorted distributions, not time series. GPU timestamps
cover dispatch. Wall includes validation, mapped copies, command recording,
submission/fence, timestamp reading, output allocation/copy; oracle comparison
is after wall timing. Packet unpack is outside timing. This is a standalone
synchronous coherent-memory benchmark, not resident live motion-to-photon.

Coverage
38 existing corpus cases plus36 generated partial/mixed-tile/high-Q/per-tile-Q
cases and48 explicitly constructed three-word NONconstant packets (every AC
group of every channel). Invalid extra full-word padding must fail CPU validation
before any dispatch. Total122 valid cases. Partial dimensions include1x1,1x17,
17x1,15x33,17x19,31x31,32x32,65x47 and576x640. Generated scenes include per-tile
constant colors, checkerboard constant/noise tiles, one-bit perturbations, and
pixel-frequency black/white checkerboards, with Q1/8/17/1024 map entries.
Original build/ and host.json used the initial74 valid cases. Reviewed build adds
48 targeted cases. build-preflight/ preserves an invalid padding fixture caught
by CPU validation; no GPU correctness failure occurred in that preflight.

Integration instructions (parent owns the live source)
Use an optional decoder name coop128dc. Keep default coop128 unchanged until
live verification. Add this shader to common/codec_test/vr444/shaders/ and compile
-DDECODE=1 -DWORKGROUP=128 to assets/codec-test/decode-coop128dc.spv. Copy this asset
in test_decoder.cpp; allow the new decoder choice. test_gpu_decoder.cpp must map
coop128dc to128 lanes and preserve its existing descriptor/push layout.
GpuSession may accept coop128dc for readback decoding; select encode-coop128.spv
for the encode pipeline and decode-coop128dc.spv for decode. Do not create a
constant encoder. Keep host encoder CLI choices separate from decoder choices.
Record selected decoder kernel in run evidence and preserve baseline A/B runs.
No production source or frozen artifact was changed by this experiment.
