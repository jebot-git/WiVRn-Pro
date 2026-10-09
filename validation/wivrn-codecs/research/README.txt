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
