# Rolling codec research workflow

This workbench accepts new WiVRn-Pro codec, GPU, transport and compositor research throughout the active research window. It produces reproducible intake snapshots and actual-byte experiment receipts without choosing a neural architecture or qualifying a codec for release.

Read [CODEC-DEVELOPMENT.txt](../../../CODEC-DEVELOPMENT.txt), [the experiment contract](CONTRACT.md), and the existing integration latency policy before interpreting results. The research overlay expects its recorded working baseline; these standalone tools do not apply it or build native/Android peers.

## Declared research capability

The user has declared rooted Quest Pro eye tracking decoupled for research: **independent left/right foveating regions and per-eye gaze/movement prediction are in scope**. Treat this as an assumed capability pending a device API/capture receipt. See [EYE-TRACKING-RESEARCH.md](EYE-TRACKING-RESEARCH.md) and [research-assumptions.json](research-assumptions.json). Rolling reviews should consider these hypotheses while retaining coherent stereo, explicit quality comparisons and measured latency/cadence gates.

## Parallel alternate-protocol workstream

The user has also authorized dedicated parallel research on alternate host/client protocols for a Magisk/Singularity-rooted Quest Pro, Intel NUC 12 Extreme i7, and user-confirmed direct 2.5 GbE Ethernet to an ASUS RT-AXE7800 with 6 GHz available. Read [PROTOCOL-RESEARCH.md](PROTOCOL-RESEARCH.md), the [initial four-agent study](protocol-research/20261009T090350Z.md), and [protocol-research-plan.json](protocol-research-plan.json). Carry the transport, topology, Linux/radio and integration hypotheses into rolling reviews while preserving the same evidence and qualification boundaries.

## Research window

The user described 270 minutes remaining at 2026-10-09 07:02:40 UTC. The reconciliation checkpoint is **2026-10-09 13:32:40 Europe/Prague (11:32:40 UTC)**. See [window.json](window.json). This checkpoint organizes evidence; it does not terminate other agents or promote a branch.

The initial [snapshot](snapshots/20261009T070628Z.json) contains 29 research branches plus `feature/custom-codec`, 28 distinct tips, and their 39 reachable commit objects. It was collected through GitHub rather than the unavailable execution environment. The snapshot contains source identities, not new performance measurements.

## 1. Refresh and pin the candidate inventory

From an existing checkout, explicitly fetch advertised commits into its object database:

```sh
python3 validation/wivrn-codecs/research_workflow/intake.py snapshot \
  --repo /path/to/WiVRn-Pro --fetch \
  --previous validation/wivrn-codecs/research_workflow/snapshots/20261009T070628Z.json \
  --output /path/to/evidence/snapshot-UNIQUE-UTC.json
```

Use a new output filename per run. The collector does not checkout, merge, or update the other agents' branches. It records new, moved and removed refs, exact SHAs, aliases and ancestry. Without `--fetch`, cached remote refs are explicitly labeled and cannot establish remote branch removal. Incomplete discovery or a bounded/incomplete graph is evidence of an incomplete collection, not evidence that a candidate disappeared.

Branch-family labels are advisory. Review new or unknown hypotheses manually. Keep the `research-workflow` infrastructure branch in the inventory but exclude it from codec ranking. Pin each evaluation to a snapshot; do not silently compare a moving branch head against older measurements.

When shell execution is unavailable, GitHub connectors can collect every page of repository branches and pinned Git commit/parent objects into the same snapshot schema with `source: github_api`. Mark completeness truthfully. Reuse exact SHAs for subsequent file reads. Append snapshots and review notes on this workflow branch only; do not edit active research branches.

## 2. Run actual-byte codec experiments

Adapters receive explicitly supplied argument arrays; no candidate is executed merely because a branch exists. Both sides must implement the same declared dimensions, RGB8 layout and bitstream contract.

```sh
python3 validation/wivrn-codecs/research_workflow/run_pair.py \
  --left /path/to/left.rgb --right /path/to/right.rgb \
  --width 576 --height 640 \
  --encoder-argv '["/path/to/encoder", "{input}", "{output}", "{width}", "{height}", "{eye}"]' \
  --decoder-argv '["/path/to/decoder", "{input}", "{output}", "{width}", "{height}", "{eye}"]' \
  --output /path/to/evidence/pair-UNIQUE \
  --stereo-payload-cap 262144
```

The payload cap is an explicit experimental configuration, not a universal WiVRn or UDP limit. The runner writes a receipt with actual per-eye/stereo encoded sizes, source/bitstream/reconstruction hashes, reconstruction differences and whole-pair admission. Rejected dense pairs remain in the results. Process startup and file-I/O timings are offline diagnostics; they are not live encoder, GPU, transport, headset or motion-to-photon benchmarks. Payload bytes are not wire bytes. This harness supplies no trained model and makes no claim of a new codec performance win.

## 3. Bind receipts to reproducible experiments

Copy [example-run.json](example-run.json), fill in the exact candidate, reference, executed harness and model identities, configuration/data digests, scene/session split and evidence domain, then validate:

```sh
python3 validation/wivrn-codecs/research_workflow/manifest.py /path/to/run.json
```

An example marked planned is not a completed measurement. Unknown data stays null. Pair receipts do not automatically populate or qualify a run manifest: the operator must bind them to the code/binary identities and measured population actually used. Training and held-out scenes/sessions must be disjoint, including correlated stereo eyes and neighboring frames.

Maintain separate tables for correctness, simulation/offline bounds, host timing and headset timing. Do not train a single ranking target on incompatible metrics. Record server cost, actual wire bytes (including the chosen packetizer and FEC), complete-stereo cadence, source age with valid clock basis, quality and presentation latency separately. Do not sum stage quantiles.

An exact match to decoded Q8 reference pixels does not mean source-image losslessness. Changes to quantization, foveation, neural reconstruction or quality must define an explicit new comparison arm. Stereo frame/warp coherence, memory bounds and malformed-input checks remain mandatory.

## 4. Update findings as branches evolve

For each newly discovered or advanced tip:

1. Identify its hypothesis and ancestry; distinguish inherited work from the new delta.
2. Read the pinned implementation, validation receipts and stated limitations.
3. Recheck affected correctness and interoperability cases when execution is available; record unavailable checks explicitly.
4. Run matched comparisons only with compatible workload, quality, device and timing populations.
5. Append a commit-scoped review and revise any earlier priority that the new evidence changes.

The prior 2,223,424-byte RPT stereo versus 262,144-byte datagram cap mismatch is a regression input for the relevant pinned revisions, not a permanent limit assumed for future branches. Re-evaluate admission/fallback and count rejected or stale pairs against cadence. Keep queue policy, FEC, sockets and compositor changes controlled while attributing codec improvements.

Use learning initially where measured evidence justifies it: encoder decisions, rate allocation, a small intra-only codec, or an offline experiment ranker. Keep architectures replaceable. A ranker suggests measurements; it does not turn predictions into evidence. A full neural decoder needs sustained Arc/Quest measurements including renderer contention and synchronization.

## 5. Reconcile at the checkpoint

Refresh refs, record completed and unfinished candidates, preserve evidence domains and unresolved blockers, and shortlist reproducible experiments against a common pinned baseline. Favor the existing target metric: complete-stereo earliest headset receipt to observation after successful xrEndFrame, p95 strictly below 15 ms, 576x640 per eye, 90 Hz offered, and at least 88 unique stereo submissions/s, with the rest of the inherited policy retained.

The inherited qualification transport is TCP over ADB reverse. Datagram simulation and localhost transport measurements are different workloads. Diagnostic instrumentation and selection models cannot qualify promotion. Both repeated candidate runs must meet the applicable policy; host costs and source freshness still matter outside the narrow headset metric.

## Validation

```sh
python3 -m unittest discover -s validation/wivrn-codecs/research_workflow -p 'test_*.py' -v
python3 validation/wivrn-codecs/research_workflow/manifest.py validation/wivrn-codecs/research_workflow/example-run.json
```

The isolated GitHub Actions workflow runs these standalone tests. They check the research tooling, not native WiVRn builds, neural quality, GPU throughput or headset latency. Follow the existing research suites separately for actual codec changes.
