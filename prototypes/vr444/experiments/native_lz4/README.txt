Native LZ4 WPC1 viability experiment

This standalone CPU prototype does not alter WiVRn wire routing or its installed
decoder. It ports the 0.7 WPC1 LZ4/raw wrapper to C++ and uses pinned official
LZ4 1.10.0 (vendor/provenance.json, BSD-2-Clause license). Zstd method2 is rejected.
CRC detects corruption, not malicious traffic or authenticity.

The nested wrapper is now capped at16MiB total: maximum input16MiB-24bytes.
This deliberately tightens the older Python experiment's16MiB input limit so
the wrapper fits the WCT maximum body. It accepts identical LZ4/raw wire data
inside this tighter bound. Full framing is checked before output allocation;
native LZ4 uses its bounded decompressor and exact output-length check, followed
by whole-output CRC. Encoding escapes to raw when compressed framing cannot save
bytes. Records use stateless blocks; there is no temporal dictionary/history.

ARM64 optional CRC uses ARMv8 CRC32 instructions (IEEE polynomial, not CRC32C)
only when Linux HWCAP_CRC32 is present; otherwise it retains table CRC. The
target("crc") function isolates the instructions from the baseline binary.
Self-test checks the known123456789 vector, table/hardware agreement, roundtrips,
truncations, reserved/invalid fields, seeded mutations and maximum-size raw escape.
Accepted mutations must decode to the original bytes; rejection is also valid.
Workspace vectors retain capacities after warmup; this is not zero-copy decoding.
Input spans overlapping encoder wire/scratch or decoder output are rejected before
mutation. CLI file length is bounded before allocation and checked again after read.

Build and reproduce (output paths must be separately selected for fresh results):
  bash prototypes/vr444/experiments/native_lz4/build.sh /tmp/wpc-build
  /tmp/wpc-build/wpc-asan self-test
  python3 prototypes/vr444/experiments/native_lz4/cross_check.py /tmp/wpc-build/wpc-host CORPUS
  /tmp/wpc-build/wpc-host bench CORPUS 21
Android binaries wpc-android-table/wpc-android-crc have the same CLI and link
libc++ statically. They require no root, app installation, tracking or worn HMD.
No frequency/thermal changes are made. Primary agent controls ADB deployment.

CORPUS contains*.payload files; corpus-manifest.json maps names to original
sources and SHA256. Current56 inputs match the 0.7 wrapper experiment:24 sparse
hello_xr Q8 captures,12Q1,8HEVC,6raw and6synthetic gradient/grid/noise Q1/Q8.
These are not a representative VR content corpus. HEVC captures have extensive
trailing zero padding; their unusually high compression must not be generalized.

Every measured job warms three times and then runs21 exact-checked repetitions;
job order is fixed-seed shuffled. Timing includes framing/copies/CRC and capacity
reuse, excludes file I/O and exact comparison, and uses local steady_clock.
Separate CRC/copy probes are diagnostic, not additive pipeline measurements.
Results distinguish mean of per-sample medians from maximum per-sample p95.
Host/Quest clocks are never subtracted from one another. The optional break-even
model assumes serial host encode+Quest decode and ideal payload serialization,
not measured WiFi, ADB, QUIC, full codec latency or motion-to-photon.

Integration gates: negotiate wrapper method/bounds, authenticate transport,
skip compression when byte savings cannot repay CPU/deadline cost, validate
representative moving scenes, preserve eye/frame/warp identity, and measure the
complete pipeline at equal wire budgets. Raw escape bounds expansion but still
costs compression/CRC/copies. Do not enable this blindly for incompressible frames.
