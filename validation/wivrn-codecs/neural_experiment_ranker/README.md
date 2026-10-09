# Advisory neural experiment ranker

This runnable, standard-library prototype trains three small tanh neural networks to estimate **one measured scalar in one matched comparison cohort**. It orders proposed experiments for human review. It never chooses a final codec, declares a gate pass, starts hardware, changes device settings, or trains a codec.

The branch starts from workflow commit 205e172f20044d717eb15cd3419981756be847f1. Existing prototype branches and strict research-workflow v1 manifest semantics are unchanged. A ranker dataset/model/shadow result is a separate versioned sidecar, not a completed codec run manifest. No research dataset or pretrained model is bundled. The twelve captured pairs reused by existing studies cannot meet this prototype's minimum independent training support.

## Run

Python 3.10 or later; no packages, downloads, credentials or accelerators:

    python3 validation/wivrn-codecs/neural_experiment_ranker/ranker.py --help
    python3 validation/wivrn-codecs/neural_experiment_ranker/ranker.py train dataset.json --out model.json
    python3 validation/wivrn-codecs/neural_experiment_ranker/ranker.py shadow model.json planned-pool.json --out shadow.json

Outputs are exclusive-created. Both train and shadow accept a fixed seed; shadow accepts --round-index for the round-robin baseline. Training uses a fixed --epochs budget (default 240); no test-set stopping or tuning occurs. Output status can be abstained. Shadow recommendations are advisory even when training succeeds.

The tiny end-to-end fixture is **synthetic CI data only**, including its fake commits and hashes:

    python3 validation/wivrn-codecs/neural_experiment_ranker/demo.py --out /tmp/ranker-synthetic-demo
    python3 -m unittest discover -s validation/wivrn-codecs/neural_experiment_ranker -p 'test_*.py' -v

The demo writes dataset.json, pool.json, model.json and shadow.json. Direct CLI use of these fixture files requires --allow-synthetic-fixture. Fixture models are rejected for nonsynthetic inference; fixtures cannot claim host/headset evidence. This verifies plumbing and learning on invented values, not research effectiveness.

## Dataset sidecar

Use demo.fixture() as a structural example only, replacing all fixture identities and provenance with real, reviewed inputs. Exact fields are checked by validate():

- schema_version: 1; kind: neural_experiment_ranker_dataset; explicit synthetic_fixture.
- cohort binds evidence_domain, metric, unit, direction, exact baseline_sha/harness_sha, comparison_config_sha256, dataset_index_sha256, device_profile_sha256, population_sha256, quality_contract and transport_profile. Each row repeats its canonical cohort SHA-256. Compare only one cohort at a time; build separate files/models for different domains, quality, transport, hardware, populations, baselines or harnesses. The comparison configuration identifies shared controls, while candidate code is pinned per row.
- features are ordered name/definition/availability records. availability must be before_measurement. Use only information actually available before the proposed experiment. Identity/split/metric names are forbidden features. This syntactic check cannot discover a target proxy disguised under another name; semantic feature and provenance review remains required. Never use future gaze, test receipts, post-experiment timings or counterfactual hindsight as causal inputs.
- rows contain id, candidate_sha, cohort_sha256, scene_id, session_id, participant_id, correlation_id, split, finite numeric features, value, priority (0..100), receipt_type and artifacts_sha256. Missing observed values are null and counted, never zero-imputed. Planned rows require value: null. Observed rows require input and observation hashes.
- receipt_type must match the evidence domain: real_byte_correctness, simulation, host_measurement or device_capture. Synthetic fixtures use synthetic_fixture. Headset labels additionally require device_capture, clock, configuration and population artifact hashes. The tool checks structure and hash syntax, **not artifact availability, authenticity, clock calibration or measurement truth**. Offline adapters/host timings cannot be relabeled as device_capture.

Supported metrics are explicit in METRICS. Serialized bytes and mismatch counts are offline correctness; modeled receive-to-submit belongs only to simulation; host encode/queue/decode costs belong only to host timing; measured headset decode/receive-to-submit and unique stereo cadence belong only to headset timing. Timing units and minimization/maximization direction are fixed. No aggregate "codec score" mixes these domains. Missing headset values remain missing. Source/gaze age is intentionally not accepted as a target until its metric/clock semantics have a separately reviewed extension.

Scene, session, participant and correlation identifiers must be stable across aliases, eyes, adjacent frames and repeated measurements. Every identifier is forbidden across splits. Transitive connected components across ANY of these identities define independent units; duplicated rows, shared captures, branch aliases, baseline repeats and multiple candidate outputs do not add independent units. Components include missing-label rows so these cannot break a correlation bridge.

Minimum observed independent units are 20 training, five validation and five test. Freeze all splits before training. This intentionally abstains on the existing small shared capture corpus. Participant separation is enforced for every dataset, a conservative restriction. Missing participant or scene provenance cannot be replaced by invented independent IDs. Planned rows may accompany training data; their identities remain eligible for later shadow ranking. Frozen train/validation/test identities, including missing-label rows, cannot reappear in planned pools.

## Model, uncertainty and controls

Each network has one eight-unit tanh hidden layer and a linear scalar output. Three fixed-seed networks bootstrap whole training components and use group-balanced losses/normalization. Training-only statistics scale features and target; validation/test observations never enter weights or normalization. Validation must improve group mean absolute error over the training-fitted constant baseline by at least two percent before neural recommendations are enabled. Test error is reported only; do not repeatedly use it to choose models or tune features.

Minimum counts and a validation gain are preliminary safeguards, not proof of generalization. Five validation components provide weak uncertainty evidence. The reported half-width is max(two ensemble standard deviations, validation p90 group absolute error): an **uncalibrated heuristic**, never a confidence bound, coverage guarantee or headset gate-pass probability. Features outside training ranges abstain; range membership alone does not establish distribution compatibility.

Shadow outputs preserve null observed_metric_value, separate estimated_metric_value, uncertainty, support counts and abstention reasons. They also provide deterministic-priority, seeded-random, candidate round-robin and exact-candidate-SHA training-lookup orders over the same planned pool. Lookup never uses validation/test outcomes. These are unexecuted control orders; rank correlation or measurement-budget effectiveness requires a future prospectively frozen comparison. Candidate commits remain identifiers, not learned categorical features. No fallback converts predictions into observations.

Model provenance includes dataset file hash, implementation hash, cohort, feature definitions, seeds, counts and frozen identities. CLI shadow adds the exact model/pool file hashes as well as canonical object hashes. Hashes describe supplied artifacts; the executable cannot certify external receipts. Save immutable data/model/result files with their actual source commit and configuration before interpreting an advisory comparison.

## Research boundaries and next steps

Retain 576x640 per eye, 90 Hz offered, complete-stereo receive-to-submit p95 strictly below 15 ms and at least 88 unique stereo submissions/s with inherited policy as separate actual hardware requirements. Host costs, source/gaze age, quality, serialized bytes, application caps, dense rejects, stale/fallback pairs and cadence remain visible. The ranker does not solve the 2,223,424-byte RPT versus 262,144-byte cap mismatch.

Rooted Quest Pro independent gaze remains the declared quest-pro-decoupled-eye-tracking-v1 assumption. Shared/current/predicted per-eye comparisons retain causal timestamps, common presentation target, uncertainty/fallback and coherent stereo/source/warp identity. No gaze trace is bundled or used here.

Actual protocol and hardware experiments remain explicit operator actions through the separately reviewed research workflow; this module neither imports nor invokes a hardware dispatcher. A manual hook may carry the shadow artifact hash as planning context, but a recommendation is not authorization or evidence. Initial validation is the synthetic CI workflow; no headset performance or research training result is claimed.
