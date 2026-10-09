# Experiment and training-data contract (v1)

This track accepts novel neural architectures, learned encoder decisions, rate allocation, and nonlearned controls. Architecture selection remains open while branch evidence arrives. A valid manifest records evidence structure; it does not establish codec correctness, headset performance, gate passage, or promotion.

## Immutable experiment identity

`manifest.py` validates a JSON run record with `schema_version: 1`. Copy `example-run.json`, then supply the artifacts and observations actually collected. Its known base commit pins identify planning context only: repin candidate, baseline, and harness to the exact commits executed before marking a run completed. Never substitute a moving branch name.

Record the SHA-256 of the exact configuration file bytes, frozen dataset index bytes, and model file bytes. Nonlearned candidates explicitly use `learned: false`, null model hash, and `uncertainty: not_applicable`; learned models use `exploratory` or `trained_unqualified`. The architecture string imposes no model family. Configuration and dataset hashes may be null for planned or failed acquisition, never completed experiments.

`provenance.artifacts_sha256` maps artifact names to SHA-256 digests; completed runs require `encoded_streams` (one file or a frozen archive/index covering every emitted stream). Also record decoder outputs, input/capture receipts, built codec binary, backend/runtime versions, and instrumentation logs where applicable. Preserve artifacts alongside the manifest; validation checks hash syntax, not artifact availability or hash truth.

Include device models, drivers, runtimes, backend selection, render load, precision, warm-up, worker count, synchronization, clocks/calibration, and transport framing in the hashed configuration. Keep `limitations` explicit: missing hardware trials, missing stereo validation dependencies, diagnostics enabled, capture incompleteness, or unavailable pretrained-data provenance remain blockers to broader claims.

## Frozen data and comparisons

The dataset index must enumerate content hashes, scene and capture-session identities, stereo/source/warp identities, RGB layout, dimensions, quantization, timestamps with clock domains, and immutable split assignments. Split by whole scenes and sessions before training; never split adjacent frames across training and held-out data. The validator rejects any scene or session occurring in multiple splits. A pretrained model with unavailable training provenance cannot establish clean held-out performance merely because this local split is disjoint.

Compare only compatible dataset/configuration, quality contract, hardware/load, timing population, and harness revisions. Keep model and candidate revisions separate; do not pool results across changed splits, leaked scenes, changed baselines, or unrelated ancestor changes. Rank actual byte counts and observations, not estimated neural rate alone.

`q8_exact` means byte-for-byte reproduction of the established Q8 reference pixels; `lossless` means exact original input reconstruction. `new_lossy` explicitly changes reconstruction and requires a stated quality reference. Exact offline comparisons report `exact_mismatched_samples` (zero only when measured). New lossy reports name a nonnegative finite metric such as MSE. Preserve raw quality receipts; metric names alone do not establish perceptual equivalence.

## Declared eye-tracking research capability

For rooted Quest Pro research, treat left/right eye tracking as independently usable signals: one separately controlled foveating region per eye, with optional causal per-eye gaze/movement prediction toward a common presentation target. This is the user-declared assumption `quest-pro-decoupled-eye-tracking-v1`, not a verified device API or performance claim.

Follow [the eye-tracking declaration](EYE-TRACKING-RESEARCH.md) and [machine-readable assumptions](research-assumptions.json). Preserve per-eye timestamps, clock domains, calibration, validity/confidence, prediction horizon/uncertainty and fallback metadata in the hashed configuration/data index. Keep coherent stereo/source/warp identity, explicit quality arms and distinct simulated/replayed/device evidence. The strict v1 manifest schema remains unchanged.

## Real-byte adapter boundary

Each candidate must implement file-based encode(input bytes, metadata) -> actual bitstream bytes and decode(bitstream bytes, declared configuration/model) -> reconstructed bytes. The decoder runs as a separate process and receives no private encoder tensors, original pixels, or unrecorded state. The bitstream or declared model/configuration must supply all reconstruction dependencies. Encoding estimates, latent element counts, and in-memory object sizes are not serialized sizes.

For integrated candidates, every input and reconstructed output must carry the same complete stereo-pair ID, source-frame ID, and warp ID. Admit, reject, fallback, and decode the whole stereo pair atomically; partial eyes and mixed identities never count as stereo submissions. Source and returned display-target identity must also be preserved when joining pacer captures; a left-eye-only join does not establish stereo coherence.

The separate `run_pair.py` receipt is an offline artifact with real output hashes, per-eye byte sizes, and raw RGB8 reconstruction errors. It is not a completed run manifest, an instrumented WiVRn transport test, Q8/display equivalence proof, stereo/source/warp validation, or headset timing evidence. Its sum of per-eye payload bytes excludes a production stereo envelope and transport framing. Command elapsed time includes process startup and file I/O.

Invoke it with `--left L.rgb --right R.rgb --width W --height H --encoder-argv '["adapter", "{input}", "{output}", "{width}", "{height}", "{eye}"]' --decoder-argv '<JSON argument array>' --output NEW_DIR`. Each trusted adapter uses complete `{input}` and `{output}` arguments; it writes actual files. The output directory must be new. `--stereo-payload-cap BYTES` makes a whole-pair admission observation after measuring both eyes. Consult `--help` for timeout and file-size bounds. The POSIX runner is not a sandbox and does not execute newly discovered branches automatically. Review its separate receipt before transferring any compatible observations into a manifest.

## Observation semantics

Use null for an unmeasured metric. Zero is an observation and must never fill missing data. Planned records have `observations: null`; completed records have every observation key, including explicit nulls. Failed acquisitions may preserve partial observations. Completed means collection finished, including measured correctness failures.

- `serialized_bytes`: sum of actual candidate stereo-envelope bytes, including format headers/padding, before admission/fallback. `max_stereo_serialized_bytes` is the largest such envelope. Preserve component payload/header counts in the byte receipt.
- `wire_bytes`: measured emitted transport bytes for the stated layer, including framing/retransmissions where measured and any fallback payloads; null for file-only tests. State the measurement layer in configuration. Never infer wire size from latent rate or compressed payload alone.
- `offered_pairs`: all input stereo pairs; `rejected_pairs`: terminally dropped pairs; `fallback_pairs`: pairs admitted through the alternative path. Rejected and fallback populations are disjoint. Every `over_cap_pairs` event needs one of those dispositions. Offered rate and dropped pairs remain in cadence accounting.
- `server_encode_p95_ms` and `server_queue_p95_ms` exclude client work. `headset_decode_p95_ms` and `headset_receive_to_submit_p95_ms` are reserved for measured headset evidence. Stage timings cannot be added as independent p95 values or treated as end-to-end latency.
- `population` states the exact inclusion/exclusion rule, measurement window, and server/headset sample-pair counts. Archive sample logs and quantile method. Quantiles over selected successful pairs must not hide rejected, stale, omitted, or late pairs.
- `unique_stereo_submissions_per_s` counts distinct coherent stereo submissions over the stated window, not repeated frames or model choices. `source_age_p95_ms` requires one of the declared clock bases; synchronized server/headset time needs an archived calibration and uncertainty estimate. Unknown clock mapping means null source age.
- `stereo_identity_verified` records an actual check, and may be false. Quality failure or identity failure remains evidence; neither can be silently relabeled as a pass.

Evidence types are distinct: `offline_correctness` uses execution `offline`; `simulation` uses `simulation`; `host_timing` and `headset_timing` use `hardware`. An offline oracle or modeled datagram run cannot fill measured headset timing fields. Native TCP, TCP over ADB, modeled datagrams, and hardware datagrams are different transport profiles.

## Gate reference and checks

`cpu-rgb444-sub15-v1` refers to 576x640 pixels per eye at 90 Hz offered, measured receive-to-submit p95 strictly below 15 ms, and the inherited minimum 88 unique stereo submissions/s. This is not optical motion-to-photon latency. Keep host encode cost and source age separately visible. This module verifies the reference profile only; repeated-run policy, correctness, full sample populations, actual hardware evidence, quality compatibility, and integration review remain necessary. No promotion or gate-pass flags are accepted.

Run `python3 validation/wivrn-codecs/research_workflow/manifest.py validation/wivrn-codecs/research_workflow/example-run.json` and `python3 -m unittest discover -s validation/wivrn-codecs/research_workflow -p 'test_manifest.py'`. Unit-test hashes and measurements are synthetic fixtures. The planned example contains no measured values.
