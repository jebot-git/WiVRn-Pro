Live acquisition, replay and development selection
=================================================

SELECTION.txt records the decision and its limits. selected-branches.json pins
the chosen implementation branches. No production configuration is promoted.

evidence-20261009.tar.gz contains 572 unchanged files: the six executed plans,
full small CPU/GPU/timeline/wireless acquisition logs, and the exact native
selector/output files consumed by native_selection_summary. evidence-manifest.json
records every path, size and SHA256 plus the archive hash. Extract into an empty
directory. Native trace inputs compress from about 107 MB; the archive is 8.3 MB.
No native pixel captures, HEVC files, APKs, prior-capture tar, or private config
bytes are included. Full 2.66 GB native acquisition remains at the original local
paths recorded in the manifest. Their original hash receipts remain available.

The two executed root-owned acquisition wrappers are preserved unchanged in
../headset_review_smoke/native_campaign.py and wireless_campaign.py. Ordinary
component acquisition used the existing ../headset_review_smoke/run.py. They
are not invoked by replay. Plan and receipt hashes bind their acquisition inputs.

After extracting the archive to /tmp/sub15-live-selection-replay, from repo root:

python3 prototypes/vr444/experiments/native_selection_summary/summarize.py \
  --selector-root /tmp/sub15-live-selection-replay/native-device \
  --output-root /tmp/sub15-live-selection-replay/native-output-device \
  --out /tmp/native-summary.json --text /tmp/native-summary.txt

python3 prototypes/vr444/experiments/android_gpu_comparison/triage.py audit \
  --plan /tmp/sub15-live-selection-replay/gpu-plan.json \
  --run-directory /tmp/sub15-live-selection-replay/gpu-device \
  --out /tmp/gpu-summary.json

python3 prototypes/vr444/experiments/android_timeline_qualification/audit.py \
  /tmp/sub15-live-selection-replay/timeline-plan.json \
  /tmp/sub15-live-selection-replay/timeline-device --out /tmp/timeline-summary.json

python3 prototypes/vr444/experiments/wireless_pair_probe/audit_worker.py \
  /tmp/sub15-live-selection-replay/worker-plan.json \
  /tmp/sub15-live-selection-replay/worker-device \
  prototypes/vr444/experiments/live_branch_selection/inputs/rowblocks-workers-host-benchmark.json \
  /tmp/worker-summary.json

python3 prototypes/vr444/experiments/wireless_pair_probe/audit_campaign.py \
  /tmp/sub15-live-selection-replay/wireless-plan.json \
  /tmp/sub15-live-selection-replay/wireless-device \
  prototypes/vr444/experiments/headset_review_smoke/wireless_campaign.py \
  . /tmp/wireless-summary.json

The worker canonical reference is an exact git-show copy from rowblocks-workers
260a1a22, path prototypes/vr444/experiments/rowblocks_workers/results/benchmark.json, SHA256
84d49986c379a988b17dfd92023807595572328c6b2ca3e43e2cee9d43811fda.
It supplies canonical source/byte identities; its host timing is not Quest data.

GPU audit also needs the unchanged binaries/corpora/shaders at original plan
paths or a device-relative --input-root. Timeline takes --binary if relocated.
The new wireless campaign auditor likewise checks exact original qualified
artifacts. Those build artifacts are retained locally, not uploaded as source.
Historical implementation/source pins are verified from Git; fetch research
branches if replaying from a narrow or shallow clone. Do not relabel missing
artifacts as a successful full replay. Native telemetry subset replay is portable
with this checkout; external build/pixel gates remain acquisition observations.

validation.json records the consolidated checkout's actual replay/regression
results and scope. It does not declare a live performance target pass. Each
component report keeps detailed device identity, raw populations, limits and
immutable implementation provenance. None of the host replay tools uses ADB.
