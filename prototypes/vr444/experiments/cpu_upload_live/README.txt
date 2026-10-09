CPU output destination experiment for the live WiVRn codec test decoder

The production integration source is client/decoder/test_decoder.cpp in the
isolated .worktrees/wivrn-codec-tests source. No GPU decoder or codec grammar was
changed. The parent integration task owns copying that source into the overlay,
runner options, qualification checks and build receipts.

Configuration
files/codec-test-cpu-output.txt accepts exactly one token:
  cached-copy    Default when the file is absent. Decode into the private cached
                 RGBA vector, then memcpy to the existing coherent staging slot.
  mapped-direct Decode directly into that same coherent staging allocation.
                 Requires backend cpu-neon, kernel direct-neon and actual ARM NEON.
Empty, extra-token and unknown configurations fail initialization. A mapped-direct
request with any other decoder backend also fails initialization.

Ownership and submission
Only a slot with no published handle is writable. The decoder worker finishes its
previous upload fence before accepting another job; an uncertain upload stops the
worker and teardown drains the queue as before. direct::decode retains the existing
bounded parser and overlap checks. A malformed coefficient can alter private staging
but cannot reach command submission or image publication. The next successful decode
fully overwrites the output. The same optimal RGBA8 SRGB image, transfer command,
barriers, completion fence and sampling policy remain in place. HOST_VISIBLE and
HOST_COHERENT are required and verified. The allocation preference is unchanged
between arms; HOST_CACHED is reported, not assumed or newly required.

Telemetry on every successful decode event
cpu_output_mode: cached-copy or mapped-direct for cpu-neon, not_used otherwise.
staging_memory_type, staging_memory_flags: actual VMA allocation values for the
selected image slot, including GPU backends, whose staging buffers already exist.
staging_copy_ms: the existing measured memcpy for cached-copy and other upload
paths; exactly zero for mapped-direct and resident GPU.
cpu_decode_ms: existing scope, including preflight and destination preparation.
capture_source: not_captured unless frame % 90 == 0. Captured frames report
cpu_cached_output, cpu_mapped_staging_snapshot, gpu_output_readback, or
decoded_cpu_output for other nonresident backends.
capture_snapshot_ms: private vector allocation/resize plus staging memcpy for
mapped CPU and resident GPU captured frames; zero where no such snapshot occurs.
The snapshot cost remains inside decode_upload_ms and complete receive-to-submit.
The synchronous capture file write remains before publication and outside the
existing decode_upload_ms boundary. No logging or sampling work was removed.

Mapped CPU capture copies exact CPU-produced staging bytes to private owned pixels
after the upload fence. This is not an image readback or displayed-pixel evidence.
The capture file consumes that owned copy, so subsequent staging reuse cannot alter
it. Cached-copy capture continues to consume its existing private CPU vector.

Validation
run-tests.sh compiles destination_test.cpp against the current live direct.cpp and
codec.cpp with Clang ASAN/UBSAN. It exercises real anonymous mapped memory bounded
by inaccessible guard pages, non-tile-aligned dimensions, dense noise, checkerboard,
sparse input, constant frames, global/per-tile quantization, scalar/vector paths,
exact reference pixels and owned snapshots across staging reuse. A deliberately
malformed last tile demonstrates a late parse failure after an earlier tile wrote
bytes, then checks exact full replacement on the next successful decode.

2026-10-09: all 98 checks pass on x86-sse2 with ASAN/UBSAN. LeakSanitizer is disabled
only for the sandbox invocation (ASAN_OPTIONS=detect_leaks=0), because this sandbox
runs under ptrace and LSan terminates after otherwise successful checks. The test
also cross-compiles for Android ARM64 with -Wall -Wextra -Werror. The full Android
test_decoder.cpp translation unit passes -fsyntax-only using its cached Debug
compile_commands.json entry, actual Android/Vulkan/VMA/OpenXR headers and existing
-Werror flags, with no diagnostics. No object/dependency output was written.
All 22 latency_runtime unit tests pass, including the new output, memory flags,
sampled capture source, snapshot clock and missing-evidence checks. No device test,
Vulkan transfer validation, link, APK build or live latency result is claimed by
these checks. Parent integration owns the APK build and live plan.

Previous standalone Vulkan evidence is preserved in
validation/results/latency-research-0.14.0/decode/REPORT.txt: byte-exact Quest mapped
output and actual SRGB image readbacks passed, and the sparse captured corpus
improved mean case median decode-through-upload by about 0.23-0.25 ms. Dense-content
and paired-tail outcomes were mixed. That private-device/queue experiment does not
predict a fixed live gain, VMA memory type, or attainment of the sub-15 ms target.
Qualification still needs repeated full stereo receive-to-submit measurements and
correctness gates. Optical motion-to-photon latency is a separate unmeasured goal.
