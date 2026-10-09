Shared offline research suite

Run from any checkout containing this exact source snapshot:
  python3 validation/wivrn-codecs/research_suite.py --list
  python3 validation/wivrn-codecs/research_suite.py --out NEW_OUTPUT_DIRECTORY
Use repeated --task NAME only for a scoped rerun. The receipt lists the selected
tasks; a scoped pass never represents full-suite completion.

The initial catalog combines eight existing Python test collections and nine
C++ safety/contract programs. C++ builds use this checkout's source-overlay,
not a separately edited native tree. ASan and UBSan are enabled and fatal;
LeakSanitizer is explicitly disabled because the managed execution context may
use ptrace. This is not a memory-leak qualification. Compiler and Python identity,
commands, logs, source hashes and artifact hashes are retained.

No headset operations, application installation, network configuration, live
server, performance benchmark or automatic promotion is performed. A missing
compiler, timeout, zero Python tests, assertion failure or source edit while the
suite is running invalidates the result. Output directories must be new.

This suite is a common regression baseline. Candidate teams add their concrete
shader, compression, transport, pacing and lifetime tests as reviewed changes
arrive; planned or unsupported coverage is never counted as executed. Standalone
CPU tests cannot qualify Android NEON, Vulkan image behavior or displayed output.
See docs/codec/research-20261009/PLAN.txt for the staged research and future gates.

Coverage-guided decoder fuzzing
  bash validation/wivrn-codecs/research/build-fuzz.sh NEW_FUZZ_DIRECTORY
  ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
    NEW_FUZZ_DIRECTORY/fuzz-decode NEW_FUZZ_DIRECTORY/corpus \
    -max_total_time=600 -timeout=5 -rss_limit_mb=1024 -max_len=65536 \
    -seed=20261009 -print_final_stats=1

The build generates 96 positive seeds spanning edge dimensions, dense/noisy,
constant and mixed-color images, Q1/Q8/Q17/Q1024, and both uniform/per-tile q.
The libFuzzer harness compares framing acceptance against the existing codec
unpacker and compares scalar/SIMD direct output against the reference decoder.
It checks output sentinels even on rejection and requires wrong-size output to
remain unchanged. Exceptions are not silently treated as successful decoding.
ASan/UBSan failures, oracle disagreement and bounds failures abort with a saved
reproducer. Run inside a new evidence directory, preserve logs, tool/compile
options, seed, corpus and source/binary hashes, and replay any finding before
classifying it. A time-limited no-finding run is not proof of parser correctness.

To keep inputs bounded for sustained exploration, the fuzz harness accepts up to
65,536 wire bytes and reconstructs at most 4,096 pixels. Larger valid frame
headers are parsed but not reconstructed. The maximum-dimension and guard-page
tests remain separate; this fuzzer does not claim their coverage. The SIMD path
on this host is SSE2, not Android NEON. It performs no Vulkan or headset work.
Coordinate CPU comparison windows: pause fuzzing during timing measurements and
record pauses; wall-clock fuzz duration is not identical to active CPU time.
