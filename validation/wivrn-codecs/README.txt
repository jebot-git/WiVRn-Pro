Current custom-codec mission: latency is mission critical
=======================================================
A correct but slower custom codec is not ready for promotion. The current custom
implementation fails the native-parity latency gate; native H.264/HEVC remain the
measured reference. See integration/LATENCY-POLICY.json and latency_policy.py.
At the existing 576x640/eye, 90-fps offered profile, provisional complete-stereo
receive-to-submit targets are median<=17ms, p95<=21ms, p99<=25ms and >=88 unique
stereo frames/s, after 2s warmup. Both repeated candidate runs must pass, preserving
pixel correctness, matched frame/warp identity, bounded resources and visual gates.
These local-clock software intervals are not optical motion-to-photon measurements.
Full qualification, representative visual testing and thermal behavior remain open.

Run the gate from this suite directory:
  python3 integration/latency_policy.py RUN_A RUN_B --out NEW_LATENCY_RECEIPT.json
The comparison runner records latency_gate and promotion_status separately from
correct evidence collection. A correctness PASS does not override blocked latency.
See validation/results/latency-critical-0.13.0 for alternate host/decode/scheduling
experiments. Earlier sections below are historical standalone-suite documentation;
current integrated runners are documented in integration/README.txt.

WiVRn dual-codec validation suite 0.3.0 — 2026-10-08

Purpose and current state
=========================
Prepared for standalone hardware evaluation against an Intel Arc A770 host and Meta Quest Pro
(Snapdragon XR2+ Gen 1) client. The suite contains executable planning, validation,
trace analysis, deterministic stimuli, packet impairment modelling, capability
collection, a C++ trace hook and an adapter contract for BOTH implementations:

  hevc-carrier 0.1.0: RGB panels in HEVC luma, neutral carrier chroma.
  vulkan-vr444 0.2.0: custom full-chroma YCoCg-R/Haar tile codec, compute shaders.

Version 0.3.0 adds hardware.py and a developer-shell Android MediaCodec harness.
It tests the actual Arc and Quest Vulkan devices, cross-device bitstream decode,
and software/QSV HEVC carriers through the Quest hardware decoder, including raw
luma stride/crop and sampled RGB reconstruction. See HARDWARE.txt for the launch
command, coverage, evidence and limitations. The frozen codec bundles are unchanged.

Both codec bundles are standalone prototypes. There is no integrated WiVRn host,
Android client APK, OpenXR presentation path, or production transport in this
release. adapter_template.py reports those blockers honestly. The host/client
adapter described in ADAPTER.txt is the remaining integration work. A green
local-smoke result does not certify hardware, image acceptability, or VR latency.

Quick start (from extracted suite root)
=======================================
Requires Python >=3.11, numpy, ffmpeg with libx265, Linux Vulkan loader/ICD.
Build requirements: C++17 compiler, CMake, glslc, Vulkan development files; Android
NDK 29.0.14206865 for the included API29 arm64 target. No pip/package installations
or privileged changes are performed by the suite. HEVC QSV requires real iHD /
oneVPL / libva support and access to the selected A770 DRM render node.

  python3 suite.py verify .
  python3 suite.py local-smoke --bundles bundles --out /tmp/codec-smoke-unique
  python3 suite.py plan --tier full --out /tmp/full.json
  python3 suite.py plan --tier soak --out /tmp/soak.json
  python3 suite.py run --adapter ./adapter_template.py --plan /tmp/full.json \
      --out /tmp/codec-readiness-unique

The final command exits 2 with status=blocked, before starting any test process.
This is the expected readiness result until the real WiVRn adapter is supplied.
Output directories for run/local-smoke must not exist; evidence is never reused
silently. Verify frozen payloads using manifest.json and SHA256SUMS.

After implementing and reviewing the adapter:
  python3 collect.py host --render-node /dev/dri/renderD129 \
      --vulkan-probe bundles/vulkan-vr444-0.2.0/linux-x86_64/vr444-probe \
      --out /tmp/host-capabilities.json
  python3 collect.py client --serial YOUR_SERIAL --out /tmp/client-capabilities.json
  python3 suite.py run --adapter /absolute/path/to/real-adapter \
      --plan plans/smoke.json --out /absolute/path/to/new-run
  python3 suite.py analyze run/trace.jsonl --out report.json

Render-node numbers are examples: confirm PCI identity first. collect.py is
read-only and leaves unavailable observations marked unavailable. It cannot
query Java MediaCodecInfo or OpenXR from a shell; the in-app adapter must do so.
The original adapter runner does not deploy automatically. The explicit hardware.py
command pushes standalone executables and a DEX jar into a unique ADB temporary
directory, runs bounded commands and removes that directory. It does not install
an APK, restart the live stack, upload a Meta channel or change qdisc settings.
Later WiVRn deployment uses a dedicated test
host/client instance; record and restore state via the adapter stop operation.

Coverage and execution order
============================
Smoke: 6 cases / 6 runs, 72 seconds of acquisition including warmup.
Full: 318 cases / 930 runs, 33,630 seconds (9h20m30s) including warmup.
Optical only: 12 cases / 12 runs, 1,500 seconds (25m) including warmup.
Soak: 12 cases / 12 runs, 22,320 seconds (6h12m) including warmup.
These exclude setup, recovery, cooldown, failed/repeated runs and adapter overhead.
Cases have stable content-derived IDs. Seeded order disperses thermal/order bias;
retain temperature/power/frequency observations so comparisons remain meaningful.

Common geometry is the previously observed 75% WiVRn setting: 576x640 per eye,
2304x2560 source per eye, 90 Hz. The HEVC carrier is 1152x1920. The suite requires
the adapter to read and verify actual geometry and dynamic warp footprints;
75% is neither a foveal area ratio nor a bitrate allocation. These are not the
earlier illustrative 3840x2160 total-stream figures. Geometry must be capability
checked before each run; a different runtime geometry needs a new plan/case ID.

Full matrix crosses three bitrate budgets, five scenes, four gaze patterns and
two network profiles for both codecs. Additional cases cover:
  - HEVC CBR 50/100/150 Mbit/s and ICQ/CQP Q18/24/30. ICQ/CQP have no bitrate
    target: report measured rate. Existing frozen atlas.py only implements CBR;
    ICQ/CQP require the future adapter's QSV setup and effective-option evidence.
  - Vulkan baseline/coop64/coop128 and protected Q4/Q8/Q16 at each budget.
    Existing offline budget search is not a real-time controller.
  - Gaze fixation/pursuit/saccades/asymmetric eyes, blink/dropout, stale samples,
    invalid angles and timestamp discontinuities; correct fallback is mandatory.
  - Clean link, jitter/reorder/duplicates, 80 Mbit/s congestion and 30 ms bursts
    of loss. Packets past a deadline must never revive an expired frame.
  - Reconnect, resizing, tracking loss, surface recreation, focus loss, fallback;
    intra codec frame recovery versus HEVC reference/IDR recovery.
  - Physical motion-to-photon runs at each rate, separately testing streamed
    scene updates and local reprojection across both eyes and three scanout
    regions. See OPTICAL.txt for the instrument protocol and executable analysis.
    Also includes 30-minute thermal/VR compositor contention
    runs at each rate for both implementations.

Synthetic fixtures are deterministic RGB444 ramps, fine colour strokes, moving
edges and cuts/noise. They do not substitute for actual VR content. Supply a
recorded, reproducible VR corpus with hashes and rights to use it; corpus cases
remain blocked without it. Generate frames on demand with stimuli.rgb_frame()
and gaze_sample(); they require no fonts or external media. Fine-stroke fixtures
are not a substitute for subjective legibility evaluation on real text/UI.

Analysis and acceptance
========================
trace.py streams JSONL into a temporary SQLite database and groups by frame/eye,
avoiding retention of all event dictionaries. Allow free scratch disk for traces
and the database (several times trace size). Metrics report nearest-rank p50/p95/
p99 with sample counts; fewer than 1000 samples flag p99 as low-confidence.
Keep warmup data for correctness but exclude it from performance statistics.
Missing frames, duplicate events, timestamp inversions, wrong stereo pairing,
stale warp metadata, lost telemetry and unavailable cross-clock timing cannot
silently pass. Dropped frames require explicit reason events and count against
the expected frame schedule. Payload and wire rates include transmitted frames
that later drop. Partial transmissions need a send_last accounting record too.

Provisional engineering targets: encode/decode p95 <=4 ms each, encode-to-
predicted-display p95 <=30 ms, drop fraction <=1%, protected RGB PSNR >=40 dB,
and measured payload <=110% of requested bitrate for rate-budget cases. These
targets are tunable in plans.py before regenerating plans; they have NOT been
validated as perceptual acceptability. Congestion/failure tests deliberately
may miss performance targets; correctness and recovery evidence remain useful.
The current runner requires no suite issues for acceptance; it does not hide
target misses in fault cases. Report them separately when evaluating recovery.

quality() computes RGB error, colour-difference RMSE and per-zone PSNR from the
actual recorded angular-footprint mask. Report codec-only (warped reference vs
decoded) AND source-to-presented (including warp/reconstruction losses) quality.
Exact equality is represented as exact=true and psnr_db=null, not JSON Infinity.
An empty protected zone is unmeasured, never perfect. Report per-eye/frame/scene
results and aggregate MSE weighted by pixel count, then convert to PSNR; do not
average PSNR. Store masks and sampled reference/decoded files with hashes.

Predicted display time is not photon time. Optical motion-to-photon requires a
physical motion reference and camera/photodiode capture, calibrated clock/event
alignment, actual display location/scanout phase and instrument error bounds.
The suite never substitutes WiVRn predictedDisplayTime for that measurement.
Do not add stage medians to obtain end-to-end latency; use matched-frame traces.

Live integration and limitations
================================
See ADAPTER.txt, OPTICAL.txt and integration/PORTING.txt. Remaining codec work includes QSV
RGB-luma packing/reconstruction with range/stride correctness, actual Quest
MediaCodec throughput/latency, or persistent Vulkan resources/compaction and
Adreno compute decode; atomically transported warp metadata; gaze handling;
OpenXR stereo presentation; bounded queues; and effective settings/readback.

The suite's packet scheduler is a deterministic oracle, not a network proxy.
The adapter must implement the injected schedule in the test transport or
provide independently measured impairment evidence; no Wi-Fi PHY-rate claim
may replace application throughput. WiVRn/OS packet headers and retransmission
bytes must be counted, with layer coverage stated explicitly. Per-foveal-region
rate attribution is only valid when coding units/slices can be mapped to the
recorded warp; otherwise label it unavailable instead of assigning 75% of bits.

Known prototype exclusions are retained in the frozen codec TESTING/EXPANSION
files. Cold software-ICD tests are correctness checks. The standalone runner now
captures Arc/Quest dispatch and MediaCodec queue timing; see HARDWARE.txt for
their restricted meaning. It is not a production-ready codec.
