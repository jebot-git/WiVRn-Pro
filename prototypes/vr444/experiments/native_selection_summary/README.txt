Native selector/output development triage — 2026-10-09

SUMMARY.txt is the compact human report; summary.json contains all ten runs,
warm full-stereo metrics, period/focus observations, joined decoder stages,
target phase, selected-source-history age, recommendations and input hashes.
The fixed six-run selector/HEVC campaign and four-run output ABBA both completed
with original evidence pass and all recorded cleanup/postconditions true.
There is no sub-15 result and no branch promotion or release qualification.

Reproduce from this repository branch, using either original acquisition roots
or an unpacked subset that preserves the campaign-relative paths:

python3 prototypes/vr444/experiments/native_selection_summary/summarize.py \
  --selector-root /path/to/native-device \
  --output-root /path/to/native-output-device \
  --out /tmp/native-summary.json --text /tmp/native-summary.txt

python3 -m unittest discover \
  -s prototypes/vr444/experiments/native_selection_summary -p 'test_*.py' -v

The inputs directory contains byte-exact copies of both executed plans, the
historical 70-file build receipt and the executed campaign wrapper (text only).
No acquisition command is run by this analysis. Campaign receipt SHA256s are
pinned in the script; plans, summaries and raw inputs are transitively bound.
The report's input_manifest lists every file read as namespace/path, byte count
and SHA256. selector/output namespaces are the two CLI roots; inputs is the
small companion directory; repo is this checkout. Only files listed in that
manifest are needed. JSONL traces and logs compress substantially. Capture
images, video, APKs, tar files and private configuration bytes are not read.

Replay revalidates every presentation, runtime telemetry, candidate snapshot
and host source trace with the exact acquisition analysis sources. It verifies
saved cleanup/config/source postconditions and binary/helper hash consistency
with the historical build receipt. It does not rebuild, rehash the external
native checkout/APK/server, independently re-decode HEVC or repeat pixel gates:
those remain explicitly identified original acquisition evidence. The hashes
are provenance consistency, not an authenticity/security proof.

All ages use the headset-local clock and earliest receipt among both eyes;
they are not pooled per-eye latency. Two-second warmup keeps global seen-frame
history. Per-eye decoder timings are joined on frame/eye/warp and separately
labeled. Presentation stage durations telescope per row, but their quantiles
must not be summed. A successful runtime gate is not a performance target pass.

The first bounded run retains its original pass but is excluded from fixed
90 Hz ranking: 124 warm presentation periods are near 72 Hz, and same-app-PID
session events show focus loss/recovery. Startup transitions outside the warm
window are not counted. No cause is attributed. The second bounded run stays
90 Hz but delivers 86.202 unique stereo Hz. Closest brackets drift strongly,
so this experiment does not isolate a causal selector effect.

Native mapped-direct remains an opt-in development candidate: both repeated
warm selected-eye decode/upload medians are lower and the staging copy is zero.
Whole-stereo output p95 drifts substantially within both arms. Keep cached-copy
as control; do not claim an end-to-end mapped winner. This native VR444 NEON
mapped RGBA destination is different from research RRF/RRB LZ4 into mapped RGB.

Prioritize the default-off native publication/pacer diagnostic integration at
be5fd0bddfc471c8f2243f93b22a85283e618a32 (publication-capture branch), including
the pacer initialization fix. The current APK does not contain those changes;
source-overlay/patch reconciliation and a fresh build are required. Current
schema-2 traces cannot account for all intervening or terminal render attempts.
Offline phase/selection/source-age oracles remain diagnostic bounds, not tested
native runtime replacements. The original historical failed fifth run remains
unchanged. Current repaired analyzer basis is integration 92e8f3f, included in
the report's base 5ce9642eb12f50cefa9443c7d93fdf40d9da7353.

Actual encoded eye streams are 576x640. hello_xr's 1800x1980 render swapchains are
different inputs. HEVC reference elementary streams are Main/yuvj420p, so they
do not establish RGB444 quality equivalence. Transport is TCP over ADB reverse
over wireless ADB. Thirty-second captures, trace observer costs, phase drift,
sampled correctness and no photon measurement limit the conclusions.
