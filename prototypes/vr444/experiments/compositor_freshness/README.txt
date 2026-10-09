Compositor/application freshness diagnostic

Purpose
The CPU decoder is currently the better measured path. The 0.14.1 candidate
traces show a complete newer custom-codec pair available in 66.9–91.1% of unique
post-warmup submissions, with its predicted target about one 90 Hz period later.
This is availability, not proof of a newer scene or an achievable 11.1 ms gain.
This diagnostic joins application submission identity to the compositor/wire
frame identity before a bounded latest-complete experiment is interpreted.

Interface and containment
Only a WIVRN_CODEC_TESTS server implements the hooks. Set:
  WIVRN_CODEC_SOURCE_TRACE=/absolute/new/path/source-trace.jsonl
Missing/empty disables collection; a relative path or existing/unwritable file
fails initialization. Initialize once during pacing-factory/compositor creation,
before frame callbacks. The output is created empty during initialization.
No production source, Monado dependency, pacer decision, frame selection,
projection pose, shader, codec or networking semantics are changed.

The header-only recorder allocates 131072 fixed records once. Producers reserve
one slot using atomic operations, copy a fixed record, and return. There is no
allocation, lock, serialization, file I/O, or blocking inside append(). Hook
clock reads and record construction add overhead and remain diagnostic only.
When full, every omitted event increments the drop count. Any drop invalidates
the capture. The bounded in-memory array is append-only; it cannot wrap old
application identity into a new record.

The compositor destructor joins its encoder thread, closes trace admission,
waits for in-flight append calls, and writes header/events/footer. This wait and
all file writes occur at teardown. No frame-path ownership is extended. A second
flush is a no-op. An orderly process exit is a fallback flush, not a substitute
for clean compositor shutdown. One capture covers the first compositor lifetime;
use a new isolated server process for every run. Callback activity after close
is outside the capture, and selected client frames must be joined to the captured
host frames to prevent using an incomplete trailing interval.

For a live run, disconnect/force-stop the dedicated headset app, allow a bounded
teardown/export interval, then terminate remaining private processes. Immediate
process-group SIGTERM or SIGKILL can prevent export. Missing output, empty output,
truncated JSON, missing footer, nonzero drops, or missing selected-frame joins
fail the diagnostic; never reconstruct a passing trace from partial data.
A valid footer establishes structurally complete serialization, not fsync-backed
persistence or a measured frame-time bound. The file is not a live progress feed.

Schema (JSONL)
Header: type=header, schema=1, clock=host_monotonic_ns, capacity=131072,
        diagnostic_only=true.
Events: type=event, sequence=1..N; every event has the same flat fields:
  event: app_predict, app_point, app_delivered, app_gpu_done, app_discarded,
         app_latched, app_retired, compositor_frame, projection_layer.
  pacer_id: stable factory-assigned app-pacer identity; 0 for compositor rows.
  app_frame_id: app-pacer prediction identity; -1 for compositor rows.
  system_frame_id: compositor/wire ID; -1 unless latched/compositor/projection.
  when_ns: supplied callback timestamp (predict uses its supplied now_ns).
  observed_ns: host monotonic sample inside instrumentation. Predict samples
               after computing outputs; other app hooks sample on entry.
  display_time_ns, display_period_ns, wake_up_ns: prediction/delivery outputs,
               or compositor target/period; unused fields are zero.
  layer_target_ns: exact projection-layer timestamp (first layer in frame row).
  layer_count, projection_count, layer_index: counts/index at accepted input.
  timing_point: Monado u_timing_point enum 0..3 for app_point; otherwise zero.
  direct_projection: exactly one projection/depth layer uses existing fast path.
  projection_pose_reused: fast path uses projection poses, or squasher's first
               projection is within 3 compositor periods of its target.
Footer: type=footer, records=N, dropped=0, complete=true.

A compositor_frame row is recorded only after encoder readiness and image
acquisition pass, before command recording. It proves accepted input, not GPU
completion, encoded output or reception. Projection rows record targets and
indices without retaining swapchains, images, handles, or pointers.

Analysis
  python3 prototypes/vr444/experiments/compositor_freshness/analyze.py TRACE
or import evaluate(rows, selected_frames=None), where rows is a parsed JSONL list
and selected_frames optionally contains nonnegative integer client wire IDs.
The evaluator fails closed on schema/clock/identity errors, dropped data,
inconsistent callback chains, absent selected-frame joins, projection count or
pose-path mismatches, and unmatched projection-to-delivered-target identities.
It reports multiple app frames sharing a layer target as ambiguous ownership.
It reports application ID reuse, not scene age. All output is promotion-blocked.
Host-only delivery-to-compositor-input time is neither cross-device latency nor
application simulation/input-to-photon latency. Diagnostic overhead cannot be
subtracted from a different run to manufacture an optimization result.

Why the existing compositor should remain for the next experiment
The native single-projection fast path already bypasses layer squashing, forwards
both eye source views/FOVs/poses, and retains stereo frame identity. For multiple
layers, layer_squasher selects first-projection poses within a three-period target
window; otherwise it samples the HMD pose for the compositor's prediction. Altering
that rule can change view-space quads, reprojection, and pose alignment.

Monado multi_system_compositor calls app_pacer::latched with the delivered app
frame and the same system frame ID passed through to WiVRn. It explicitly permits
one app frame to be latched more than once. A compositor/wire frame can therefore
be new while application content is reused. Even a new app frame can render the
same simulation state; an app-instrumented input/simulation epoch is still needed.

The next bounded newest-pair selector and CPU upload optimization can be tested
without a new shader or compositor. Replacement shaders are justified only after
GPU timestamps show a material remaining bottleneck, with exact color/stereo
checks and final image validation. A custom compositor adds pose/space/layer and
synchronization risk and cannot independently establish an optical sub-15 ms
result. Prioritize complete-stereo receiver-to-submit tail latency, app-latch
freshness, and moving-scene quality together. The current untraced CPU p95 was
about 23–25 ms versus native HEVC 18.6 ms at this small test profile; neither
qualifies the user's new sub-15 ms preliminary objective.

Primary local evidence (read-only dependency)
  isolated server/driver/app_pacer.cpp: supplied app callbacks and prediction.
  isolated server/compositor/compositor.cpp: frame.rendering.id is encoded ID;
    accepted-image input and existing single-projection path.
  isolated server/compositor/layer_squasher.cpp: pose_data and 3-period cutoff.
  production source/build/monado-src/src/xrt/auxiliary/util/u_pacing.h:
    callback contract, particularly latched repeated application frames.
  production source/build/monado-src/src/xrt/compositor/multi/
    comp_multi_system.c: transfer_layers_locked and system-frame forwarding.
    comp_multi_compositor.c: delivered/GPU-done/latch/retire callback sites.
No dependency source was changed. Production source root is
/home/blux/kwin/spatial-anchoring/wivrn-pro-keepalive/source; isolated test source
is /home/blux/spatial-bridge/.worktrees/wivrn-codec-tests.

Validation commands
  python3 -m unittest discover -s prototypes/vr444/experiments/compositor_freshness -v
  clang++ -std=c++20 -O1 -g -Wall -Wextra -Werror -pedantic \
    -fsanitize=address,undefined -fno-omit-frame-pointer -pthread \
    -DWIVRN_CODEC_TESTS=1 -I .worktrees/wivrn-codec-tests/server/driver \
    prototypes/vr444/experiments/compositor_freshness/source_trace_test.cpp \
    -o /tmp/source-trace-test
  ASAN_OPTIONS=detect_leaks=0 /tmp/source-trace-test
LeakSanitizer is unavailable under this environment's ptrace/sandbox execution;
ASan/UBSan still run with only leak detection disabled. The C++ test checks
concurrent appenders, overflow accounting, teardown racing active writers,
exclusive output creation, no hot-path writes, and idempotent shutdown.
The two modified C++ translation units also pass syntax compilation with their
actual compile_commands.json flags. This is not a linked server/device test.
