Diagnostic selection research 0.14 (2026-10-09)
===============================================
native_compare.py --candidate-trace enables a bounded selection-trace.jsonl in the
dedicated test APK. It preserves the actual closest-target policy. Fixed snapshots
record all nine history slots, selected identities, original source display targets,
local runtime target, and xrWaitFrame/render/selection/submission times. The test
render thread records wait entry/return; these are API timing, not optical timing.
No image, gaze direction or pose vector is added by this trace.

Configuration is backed up/restored using codec-test-candidate-trace.txt (0/1,
default0). Up to4096 rows are written per scene, after successful xrEndFrame and
outside frames_mutex. Existing present rows carry counters, write status and
measured write duration. The trace can affect the following frame's scheduling;
its I/O is after the existing submit timestamp. Diagnostic runs are always blocked
from promotion. Compare performance using untraced repeated runs separately.

candidate_trace.py validates the actual closest-target decision, complete stereo
and alpha participation, immutable fallback metadata, timing/fence order, full
bounded joins and explicit drops. It reports whether a higher frame ID was actually
available; that is not proof of newer application content or an achievable gain.
Use --mode custom-vr444 --decoder-backend cpu-neon --selection-phase early
--fps-divider 1 --seconds 30 --bitrate 20000000 --candidate-trace. A native-hevc trace provides a reference for target alignment.
The original latency thresholds and full optical/visual/thermal requirements stand.

Mission-critical custom latency policy (0.13 development)
=======================================================
LATENCY-POLICY.json defines provisional native-parity software gates for the current
small-scene90fps profile. latency_policy.py validates complete stereo from the
earliest eye packet receipt to successful submission, including p50/p95/p99 and
unique content cadence. This deliberately avoids hiding a slower eye in pooled
statistics. It is a different aggregation from the historical0.12 comparison table.
No change may buy lower latency by mixing eyes, changing frame/warp identity,
weakening bounds/validation, or silently reducing visual quality. Both repeated
candidate runs must pass; isolated-stage speedups and simulation are insufficient.
The comparison runner preserves a valid experiment while setting promotion_status
blocked when the custom candidate fails latency. Full release readiness remains
false until the other optical, visual, robustness and thermal gates are satisfied.

Experimental client paths (0.13)
-------------------------------
native_compare.py now accepts --decoder-backend resident|cpu-neon and
--selection-phase early|after-fence. Defaults preserve resident GPU decoding and
early selection. cpu-neon is custom-VR444 only and requires the actual AArch64 NEON
backend; it rejects fallback. It consumes the existing compact wire payload,
validates framing/coefficients, decodes into private cached RGBA, copies to coherent
staging and uses the existing fenced Vulkan image upload. It changes no wire bytes,
quantization, color-space policy or stereo identity. CPU captures are pre-upload
RGBA: exact capture agreement alone does not establish displayed texture quality.

The after-fence experiment waits for the previous render before choosing the
complete frame/warp pair, retaining both previous resources through completion.
It changes the time of selection, not common_frame's frame-selection policy.
New timestamp telemetry validates the actual order. CPU processing, staging copy
and queue-submit/fence wall times are separate; unavailable GPU decode timestamps
are excluded instead of reported as zero GPU work. All these are diagnostic test
options, not production defaults or an optical motion-to-photon measurement.

Example, using the matching 0.13 APK (wear headset with tracking):
  python3 integration/native_compare.py --source SOURCE --serial ADB_SERIAL \
    --mode custom-vr444 --fps-divider 1 --seconds 30 --bitrate 20000000 \
    --decoder-backend cpu-neon --selection-phase after-fence --out NEW_DIR
Use --decoder-backend resident --selection-phase early for the current GPU control.
Compare repeated runs of the same binary/configuration with:
  python3 integration/latency_policy.py RUN1 RUN2 --build-receipt RECEIPT --out JSON
The receipt binds current host/APK hashes. Repeated evidence must have different
presentation hashes; copied folders do not count as separate runs. Raw runtime
records and read-back client configuration are revalidated. Historical native
limits can be examined with --reference-native; that mode is never a candidate.

Native comparison profile 0.12 (2026-10-08)
==========================================
native_compare.py compares actual native WiVRn QSV H.264/HEVC + Android surface
decoding against custom VR444 on the dedicated test APK. It is separate from
hevc-carrier. Example from validation/wivrn-codecs:
  python3 integration/native_compare.py --source SOURCE --serial ADB_SERIAL \
    --mode native-hevc --fps-divider 1 --seconds 30 --bitrate 20000000 --out NEW_DIR
Modes: native-h264, native-hevc, custom-vr444. Custom uses stride1, baseline host,
coop128dc/resident client, Q8 and LZ4 by default. Divider1 offers90fps at requested
90Hz display; divider2 offers45fps. Test APK geometry remains576x640 per eye.
Runtime evidence must verify the requests; native bitrate and custom quantizer
are different policies. 30 seconds is the maximum per invocation.

Use the 0.12 host rebuild: WIVRN_CODEC_COMPARE=1, enabled only in WIVRN_CODEC_TESTS,
suppresses the unrelated tracking worker for every comparison path. The APK is
unchanged from final0.11. Native activation clears inherited custom codec flags.
The runner backs up/restores dedicated app configuration and records cleanup.
No production package is installed or replaced. Wear the headset with tracking.

Metrics use first successful coherent stereo submissions, with an additional
2-second warmup exclusion. Require adequate unique pairs, valid same-clock data,
nonflat content, matching actual decoder choices and valid optional host snapshots.
Native bitstream software decoding checks content; it is not headset pixel quality.
Manually audit active QSV/MediaCodec selection using PID-scoped run-local logs.
The native_evidence.py and native_metrics.py helpers plus raw data preserve scope.
Read validation/results/native-codec-comparison-0.12.0/REPORT.txt for the measured
comparison and its limitations; full qualification and optical timing remain open.

Current development profile 0.11 (2026-10-08)
============================================
These options apply to the isolated WIVRN_CODEC_TESTS host and dedicated
org.meumeu.wivrn.codectest APK. Build and install matching current test binaries;
there is no capability negotiation or production default change.

Lossless payload wrapping: --compression none|lz4 (default none), available through
live.py and suite.py live-integration. lz4 is accepted only with --mode vulkan-vr444.
The runner configures both the host WIVRN_CODEC_TEST_COMPRESSION environment and
the client files/codec-test-compression.txt opt-in. A client configured none rejects
compressed envelopes. Older test clients reject the new nonzero envelope flag.

The sender wraps each independent VR444 payload with bounded WPC1/LZ4 only if it
saves at least 64 bytes, including wrapper overhead; otherwise it sends the exact
original payload with flags zero. No dictionaries or cross-frame dependencies
are introduced. Input/wire payloads stay within 16 MiB; the top 24 legal input
sizes bypass compression because the WPC1 limit includes its header. The receiver
checks outer CRC/identity, exact decompression and inner CRC before codec decode.
CRC is corruption detection, not authentication. Transport remains TCP over ADB
reverse. Reported byte savings concern encoded payloads, not delivered throughput,
transport framing or motion-to-photon latency.

Decoder option: --decoder-kernel coop128dc adds a 128-lane constant-tile fast path
with the existing VR444 wire format. It verifies canonical DC-only tile grammar
before direct fill and uses cooperative inverse decoding for all other tiles.
This is distinct from the earlier compact-wire experiment. Host --kernel choices
remain baseline|coop64|coop128; the decoder default still follows --kernel unless
explicitly overridden. --decoder-backend remains resident|readback. Keep the
baseline/coop128 controls for comparison; there is no universal speedup claim.

Example current comparison profile, from validation/wivrn-codecs:
  python3 suite.py live-integration --source SOURCE --serial ADB_SERIAL \
    --mode vulkan-vr444 --kernel baseline --decoder-kernel coop128dc \
    --decoder-backend resident --compression lz4 --frame-stride 2 \
    --pairing paired --seconds 20 --out NEW_OUTPUT_DIRECTORY
Use --compression none for the unwrapped control and --decoder-kernel coop128 for
the original cooperative decoder. Resolve ADB_SERIAL using adb devices; USB and
wireless endpoints can change. No root access is required by this live runner.

Optional --gaze-timing enables the existing eye-gaze feature plus bounded timing
telemetry, subject to runtime support and the headset's eye-tracking permission.
Wear the headset with tracking active for scene/gaze checks. The log records local
OpenXR poll/request/returned pose-expression times and validity, at most one row
per 100 ms and 6000 rows total. It does not record eye images or gaze directions.
A returned expression time can be future-predicted or unavailable (zero); it is
not a camera acquisition timestamp or a motion-to-photon measurement. This adds
instrumentation, not a new gaze-driven codec quantization algorithm. Cleanup
resets diagnostic collection and the dedicated test app's eye-gaze setting off.

Current pass gates require fresh decoder_kernel, decoder_backend and
compression_policy telemetry on every successfully decoded frame, matching the requested settings,
consistent compression admission/byte counts at both ends, coherent stereo and
nondegenerate captured content. Requested gaze timing additionally needs valid
schema/rate/flag provenance and at least one successful orientation-valid pose
with an available expression time. Missing/invalid gaze or active-selection
telemetry fails the run. Unworn black/flat runs remain failed even when wire
checks pass; --unattended/--scene-space View does not override runtime validity.
These smokes do not complete the 930-run qualification or optical latency tests.

Historical 0.8 additions (2026-10-08)
Color sampling is now explicit per decoder/frame: test SRGB images skip the
second shader EOTF while legacy decoders keep their existing policy. Both cached
pipeline variants survive until safe reset. Actual reprojection shaders pass72
cases on A770 and Quest using dyadic test geometry; arbitrary live mesh precision
is not exhaustively qualified. See codec-viability-0.8.0/color/REPORT.txt.

live.py and suite.py live-integration accept --meta-sr off|normal|quality.
Default off resets both compositor filter fields; normal4/quality8 are exclusive.
Fresh enabled-instance capability and submitted projection flags are recorded.
Seven worn live runs passed; flags0,4,8,0 verified. Flags are hints, not proof that
a particular Meta algorithm or subjective improvement occurred.

Historical 0.8 test APK SHA256 (not the current build):
b42f5a75a98835270ac5f347e22760c4d78a0cd361c0344967955553dfac5291
At 0.8, compact GPU, LZ4, CAS and UDP prototypes remained separate from that APK.
Current 0.11 integrates optional LZ4 wrapping and the new decoder described above;
compact-wire, CAS and UDP experiments remain separate. TCP/control is unchanged.
Full930qualification is still blocked; these are correctness/viability smokes.
The 0.8 headset then accepted wireless ADB at 192.168.1.110:5555; all seven 0.8
live runs preceded that switch and used USB ADB reverse. Do not label them WiFi.
That historical endpoint does not identify the current rooted headset.

WiVRn codec test integration — stereo scheduling profile 0.6.0
========================================================
This implements a real WiVRn host/client correctness path. It is a separate,
explicit smoke profile, not completion of the 318-case/930-run qualification plan.

Source:
  /home/blux/spatial-bridge/.worktrees/wivrn-codec-tests
Original user working tree was copied into a detached worktree before changes;
source-snapshot.json identifies its commit and original dirty patch. The original
working-tree diff still hashes identically. Production services/configuration and
org.meumeu.wivrn.spatial were not replaced. An initial APK install was rejected by
Android because the inherited suffix was hard-coded; that was fixed, then the
separate org.meumeu.wivrn.codectest package was installed successfully.

Implemented pipeline
The WIVRN_CODEC_TESTS build option plus WIVRN_CODEC_TEST mode selects a test
encoder before normal encoder policy. A shader preserves sRGB RGB values before
4:2:0 conversion. Test stream dimensions are 576x640 per eye; HEVC uses one
576x1920 three-panel luma carrier per eye. This differs from the standalone
1152x1920 combined-stereo carrier and must not be represented as the same profile.

Three modes share WiVRn's shard transport, immutable view/foveation metadata,
frame IDs and actual OpenXR projection/defoveation path:
  raw-rgba      exact bytes, no compression or video codec
  hevc-carrier  persistent Arc hevc_qsv encoder; Quest c2.qti.hevc.decoder with
                Java getOutputImage raw luma extraction; no RGB surface conversion
  vulkan-vr444  independent custom transform tiles; Vulkan compute on both devices

The 48-byte envelope validates dimensions, mode, eye, frame ID, warp hash, size
and CRC. The default paired policy admits the newest complete frame/warp-matched stereo pair,
with one active pair and at most two pending frame IDs. Both eye workers finish
before the next pair is admitted; each decoder has eight image slots.
--pairing independent retains the previous per-eye latest queue for comparison.
Pending memory is bounded; no wall-time expiry or deadline controller is implemented.
The three-frame stereo history uses arrival order so stride-12 frame IDs do not
alias one cache slot. Test stereo selection retains the last matched pair instead of
mixing eyes. Missing the initial pair does not reset a fence without submitting
GPU work. Captures/metadata are checked across host, decoder and OpenXR submit.

Scope limits that matter
* Correctness-first: default every twelfth host frame, intended 7.5 new frames/s
  if the host is actually 90 Hz. Actual decoded/presented counts are reported.
* Vulkan contexts, buffers, pipelines and fences are persistent. Host encoding
  skips the decode pass; validation checks coefficient grammar without rebuilding
  RGB. Word-based bit packing preserves the existing wire format. The resident
  client backend borrows the app Vulkan device and copies decoded pixels directly
  from GPU storage to the sampled image. CPU RGB readback is evidence-only.
  Host RGB readback, CPU tile expansion/validation and synchronous logs remain.
  These changes do not establish full-suite performance readiness.
* RGB quality is captured after host foveation and before client defoveation.
  It is not a source-to-final-displayed quality measurement. OpenXR submission
  and predicted display times are not photon measurements.
* HEVC is all-intra at a requested 100 Mbit/s total; this profile does not verify
  every CBR/ICQ/CQP option or driver rate-control readback in the qualification plan.
* Transport is TCP through a dedicated ADB reverse port, not qualified Wi-Fi/UDP
  impairment, retransmission or IP wire-byte accounting.
* Raw samples must be exact. HEVC samples must exactly match software luma decode.
  VR444 samples must exactly match the independent host CPU tile decoder. Lossy
  modes additionally have a provisional >=30 dB whole-image RGB smoke gate.
  This gate is not the protected-zone/full-suite visual acceptance criterion.

Build/rebuild
Set JAVA_HOME to the available JDK 17 and ANDROID_HOME to the Android SDK, then:
  integration/build.sh /home/blux/spatial-bridge/.worktrees/wivrn-codec-tests
The isolated CMake cache and Android dependency inputs already exist. Toolchain
and original cache paths are recorded in source-snapshot/configuration evidence.
The build script verifies the applicationId before printing the APK path.

Deploy the dedicated debug APK (does not replace the production package):
  adb -s ADB_SERIAL install -r SOURCE/build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk

Run from validation/wivrn-codecs, while the Quest is awake:
  python3 suite.py live-integration --source SOURCE --serial ADB_SERIAL \
    --mode raw-rgba --seconds 35 --out NEW_OUTPUT_DIRECTORY
--unattended attempts a head-relative View scene. It cannot guarantee rendering
when the runtime invalidates poses while unworn; black frames fail the gate.
Wear the headset for reliable live comparisons.
Actual user presence is preserved. Normal Local-space scene tests require valid
world tracking and may require wearing the headset; never bypass that validity
check to claim normal tracking coverage. The runner rejects uniform or near-uniform source captures (fewer than two sampled RGB colors or
less than 16 code values of channel range) in both presented eyes, preventing black/no-layer
frames from passing the scene gate. This is a basic sanity check, not VR quality
qualification. Initial actual presence is sent when the test client connects.

Repeat with --mode hevc-carrier and --mode vulkan-vr444; --q 1 requests lossless
VR444. The default is --q 8. Maximum duration is 120 s per invocation.
--frame-stride 1 offers every host frame (requested 90 Hz); it does not assert
90 Hz delivery. --kernel baseline|coop64|coop128 selects the host kernel;
--decoder-kernel baseline|coop64|coop128|coop128dc overrides the headset independently.
Earlier 0.6/0.8 measurements favored baseline on A770 and coop128 on Quest for
hello_xr; this is not a current universal ranking, especially for dense/noisy input. --decoder-backend resident (default) avoids decoded RGB round trips;
readback retains the persistent standalone backend for comparison.

Example high offered-rate experiment:
  python3 suite.py live-integration --source SOURCE --serial ADB_SERIAL \
    --mode vulkan-vr444 --kernel baseline --decoder-kernel coop128 \
    --decoder-backend resident --frame-stride 1 --pairing paired --seconds 35 --out NEW_DIRECTORY

Timing distributions include startup and sampled evidence overhead. Headset
receive-to-submit uses one headset XrTime clock and first successful stereo
submission only; it excludes host work and transport before first receipt.
It is not motion-to-photon. encode_cpu_wall_ms excludes source readback waiting.
The resident GPU interval includes barriers/application queue contention.
Actual unique stereo cadence and quality samples are always reported separately.
Upload failures retire the decoder without reusing pending resources; teardown
waits for queue completion or device loss. Other wait failures terminate the test
process, and external watchdogs remain necessary for a hung driver.

Additional executable experiments: persistent_bench.cpp checks CPU/GPU codec
agreement across kernels; resident_bench.cpp checks the borrowed-device backend;
texture_sample_bench.cpp plus texture_sample.comp check actual BC7/raw upload and
sampling. See TEXTURE-BENCH.txt. These are synthetic tests, not VR quality gates.

Runner isolation and recovery
Uses a private DBus, short private XDG runtime/config directories, explicit OpenXR
manifest, port 9767, no runtime-registration change, no service discovery and no
production process termination. The current test build runs the tracking worker;
eye-gaze timing remains opt-in. Loopback is permitted only for the dedicated ADB
test connection.
hello_xr and the server have external timeout watchdogs. Cleanup stops the test
APK and owned process group, removes only its reverse mapping, pulls evidence,
and writes cleanup.json. Existing reverse mappings on that port cause refusal.
A hard kill of Python can leave the test APK/reverse mapping; server/application
watchdogs still bound host work. Recover only those owned test resources:
  adb -s ADB_SERIAL shell am force-stop org.meumeu.wivrn.codectest
  adb -s ADB_SERIAL reverse --remove tcp:9767
Do not pass this correctness runner off as the stronger crash-restoration contract
in ADAPTER.txt; that is another reason the qualification gate remains closed.

Full qualification
integration/adapter.py speaks the suite probe/run/stop protocol and reports the
functional validation receipt. It deliberately blocks full qualification because
fresh target/readiness validation, calibrated clock uncertainty, bounded trace
collection, wire accounting, dynamic gaze scenarios, requested rate/kernel/network
variants, lifecycle tests, source-to-presented capture, the VR corpus and physical
optical equipment are not all implemented/available. Its run action cannot return
a fabricated completed trace. Continue using the standalone suite for its actual
validated hardware coverage; keep the full gate strict.

Additional investigation
QUEST-OPTIONS.txt: measured non-root access and unexercised root-only possibilities.
TRANSFER-OPTIONS.txt: raw/tiles, compressed textures, damage updates, local layers,
depth-motion reprojection, scene-state transfer, calculations and primary sources.
transfer_analyze.py: reproducible offline size exploration on sampled raw captures.

Review/apply to a fresh copy of the recorded baseline
  python3 integration/apply_overlay.py COPIED_BASELINE
  python3 integration/apply_overlay.py COPIED_BASELINE --apply
The first command is verification only. Both check all baseline and overlay hashes
before any writes. integration.patch covers text changes; source-overlay also
contains the frozen SPIR-V shader files. Never apply blindly to a changed source.

Stereo scheduler host tests: integration/stereo_admission_test.cpp. The live
summary includes coherent pair utilization and shared counter maxima (not the sum
of duplicate eye snapshots). ASan/UBSan and TSAN recipes/results are recorded in
validation/results/stereo-admission-20261008/REPORT.txt in the working workspace.
Paired dropping is only valid for independent raw/VR444/all-intra test payloads;
future predictive video streams require dependency-aware handling.
0.15 CPU path and bounded-selection experiments (source-only validation)
=====================================================================
native_compare.py accepts --cpu-output cached-copy|mapped-direct (default cached),
--selection-policy closest-target|bounded-newest (default closest), and
--source-trace. Mapped output requires custom-vr444 plus --decoder-backend cpu-neon.
Bounded selection requires --candidate-trace. Both diagnostic tracers and either
experimental path block promotion. All new client configuration is backed up,
read back and restored; missing trace export or inconsistent evidence fails.

CPU-LATENCY-TARGET.json adds the preliminary strict p95 < 15 ms RGB444 software
target without relaxing the original correctness/cadence/p99 policy. Existing
hardware results predate this source iteration. Source, build and device states
must remain separate; no 0.15 APK or live result exists yet. The current sandbox
blocks ADB. See docs/codec/cpu-development-0.15.0.txt for checks and next-run plan.
