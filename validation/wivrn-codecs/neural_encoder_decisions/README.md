# Neural encoder decisions: executable starter

This branch starts exact-reconstruction encoder-decision experiments with a
standard-library tanh MLP (8 causal features, default 8 hidden units, 3 outputs),
training, inference, held-out evaluation, and an explicit external native-adapter
hook. It contains no new RRF/RRB encoder, trained research model, native RRB plan
integration, quality-changing allocator, or headset performance result.
Architecture remains replaceable through the versioned plan boundary.

Read the [experiment contract](../research_workflow/CONTRACT.md),
[eye declaration](../research_workflow/EYE-TRACKING-RESEARCH.md) and
[protocol plan](../research_workflow/PROTOCOL-RESEARCH.md).
Common live hardware hooks belong to research_workflow; this module does not
connect to a device or execute discovered branches automatically.

## Bounded first run

Python 3.10+ and the standard library suffice; native hooks require POSIX/Linux.

~~~sh
python3 -m unittest discover -s validation/wivrn-codecs/neural_encoder_decisions -p 'test_*.py' -v
python3 validation/wivrn-codecs/neural_encoder_decisions/smoke.py --output /tmp/rrb32-smoke-UNIQUE
~~~

Smoke creates 30 synthetic blocks (18 train, 6 validation, 6 test), a frozen index,
arbitrary fixture byte artifacts, a 60-epoch tiny model and test evaluation. These
are executable tooling checks. Fixture bytes are not RRF/RRB encoded data and
fixture reconstruction copies are not native oracle results. CI is limited to
five minutes and runs tests, this synthetic training, and common workflow tests.
It does not run research training or connect to hardware.

## Frozen artifact dataset

core.py load_dataset(path, expected_sha256) parses the same index bytes it hashes.
All relative artifact paths must stay inside the index directory and match recorded
SHA-256. Whole scenes, sessions and identical source content cannot cross splits.
Correlated eyes and blocks share coherent source/warp IDs and split assignments.
Every pair requires matching, contiguous left/right blocks; nonfinal blocks have
32 rows. This deliberate conservative content check also rejects repeated identical
blocks across splits; curate the data explicitly rather than silently dropping them.

An index contains exactly:

- schema_version: 1; profile: "rrb32-rgb8-mode-v1".
- quality_contract: "lossless" or "q8_exact". Q8 means equality to supplied decoded
  reference pixels, not original source-image losslessness.
- evidence: "offline_native" or "synthetic_smoke".
- provenance: candidate_commit, baseline_commit, harness_commit (full immutable
  40-character IDs), and artifacts (relative path/sha256 objects). Native data needs
  at least encoder and decoder artifacts; also pin scripts, libraries, configuration,
  mode-compliance/oracle logs and capture receipts. Hashes do not prove artifact roles.
- records: blocks containing id, scene, session, split, stereo_pair_id, source_frame_id,
  warp_id, eye ("left"/"right"), block_index, width, rows, source, outcomes. source is
  a path/sha256 object for packed RGB8 block bytes. outcomes contains raw/plain/up,
  each with encoded and decoded path/sha256 objects.

All three outcomes are required. The loader counts actual encoded file bytes and
checks every decoded artifact against the source. It proves equality and byte
counts, not RRF grammar or mode compliance: preserve the native collector/oracle
receipt too. Never use latent counts or predicted rate as these files. Smoke's index
is a schema example, not a captured-data substitute.

Split before training. Reused captured stereo pairs are regression material, not
scene-generalization evidence. Keep dense/oversized cases. Fixed RRB32 is the initial
controlled profile; RRB128, foveation or altered quantization need separate arms.

~~~sh
python3 validation/wivrn-codecs/neural_encoder_decisions/core.py train \
  --index /data/frozen/index.json --index-sha256 EXACT_INDEX_SHA256 \
  --epochs 40 --hidden 8 --seed 7 --output /data/run/model.json
python3 validation/wivrn-codecs/neural_encoder_decisions/core.py evaluate \
  --index /data/frozen/index.json --index-sha256 EXACT_INDEX_SHA256 \
  --model /data/run/model.json --split test --output /data/run/evaluation.json
python3 validation/wivrn-codecs/neural_encoder_decisions/core.py predict \
  --input /data/pair/left.rgb --width 576 --height 640 \
  --model /data/run/model.json --policy mlp --output /data/run/left.plan.json
~~~

Outputs must be new. Training uses only train gradients and predicts the byte-minimal
mode; ties prefer raw, plain, then up. Inputs are RGB mean/variance,
horizontal/vertical differences, zero fraction, horizontal/vertical equality and block
height. No compressed size, future frame, gaze label or held-out outcome enters
inference. Python feature scans are not free or realtime native integration.

Controls are fixed_raw/plain/up and adaptive_bytes (try all three).
Evaluation reports counterfactual selected block artifact bytes on the matched held-out
population, not assembled stereo or wire bytes. It does not measure CPU savings.
Native adaptive controls must pay for every attempted mode; record raw fallback and
exact action semantics in the adapter receipt. Probabilities are uncalibrated.

Models record frozen index/training membership and settings.
model_canonical_sha256 hashes canonical JSON; CLI model_file_sha256 hashes the
actual loaded bytes, including formatting. Use the latter in run manifests. No test
split checkpoint/hyperparameter selection occurs.

## External adapter interface

native.py --config CONFIG.json --output NEW_DIRECTORY invokes run_pair.py's bounded,
separate encoder/decoder processes. Executable and dependency regular-file absolute
paths need SHA-256 pins. Trusted commands are not sandboxed.

The config has exactly this shape (replace every placeholder; no adapter is bundled):

~~~json
{
  "schema_version": 1,
  "profile": "rrb32-rgb8-mode-v1",
  "quality_contract": "q8_exact",
  "provenance": {
    "candidate_commit": "EXACT_40_CHARACTER_COMMIT",
    "baseline_commit": "EXACT_40_CHARACTER_COMMIT",
    "harness_commit": "EXACT_40_CHARACTER_COMMIT",
    "dataset_index_sha256": "EXACT_64_CHARACTER_SHA256"
  },
  "identity": {"stereo_pair_id": "pair-001", "source_frame_id": "source-001", "warp_id": "warp-001"},
  "width": 576, "height": 640,
  "inputs": {
    "left": {"path": "/data/left.rgb", "sha256": "EXACT_SHA256"},
    "right": {"path": "/data/right.rgb", "sha256": "EXACT_SHA256"}
  },
  "policy": "mlp",
  "model": {"path": "/data/model.json", "sha256": "EXACT_SHA256"},
  "encoder_argv": ["/opt/rrb-adapter", "encode", "{input}", "{output}", "{plan}", "{width}", "{height}", "{eye}"],
  "decoder_argv": ["/opt/rrb-adapter", "decode", "{input}", "{output}", "{width}", "{height}", "{eye}"],
  "artifacts": [{"path": "/opt/rrb-adapter", "sha256": "EXACT_SHA256"}],
  "payload_cap_bytes": 262144, "timeout_seconds": 30
}
~~~

Controls use their policy name and model: null. Encoder receives per-eye source hash,
dimensions, coherent IDs, eye, 32-row blocks and requested raw/plain/up action
(or adaptive_bytes), model identity and probabilities. It must verify these and write
actual canonical encoded eye bytes. Decoder receives no plan or source, only encoded
bytes and declared dimensions/eye; an encoder-only selector needs no decoding model.

decision-receipt.json binds config/model/plan hashes and the pair receipt. It verifies
reference reconstruction, source/plan consistency, unchanged artifact pins and counts
both complete outputs before the cap. Cap rejection retains both artifacts and
offered/rejected/over-cap counts, with zero admitted eyes. No fallback is silently used.
Bad reconstruction cannot be admitted.

decision_application_verified stays false: native parser/action receipts must prove
the adapter obeyed the plan and preserved RRB grammar/wire IDs. Requested identity
metadata is not wire-identity verification. Synthetic tests exercise separate-process
I/O, exact mismatch failure and real fixture byte caps, not native interoperability.

The two-eye payload sum excludes additional stereo envelope/transport overhead.
Wire bytes, live cadence and headset timing remain null. Subprocess times include
startup and I/O and are offline diagnostics. Invalid configs stop before execution;
runtime failures retain the underlying pair receipt and unsuccessful decision receipt.

## Next evidence

Freeze controls and genuine scene/session splits; collect matched mode artifacts
and native oracles before research training. Measure feature extraction, inference,
selected native encoding, whole-pair serialized bytes, dense cap/fallback population,
full offered cadence and compatible quality with fixed workers/load/transport.
Do not sum stage p95s or infer a hardware win from loss or byte predictions.

This first track has no gaze input. A causal per-eye allocator needs timestamped
API/capture evidence, frozen per-eye validity/calibration/clock/horizon/uncertainty,
common presentation target, participant isolation, future gaze as labels only and
explicit quality/fallback receipts. Changed reconstruction is a new_lossy arm.

Target integration must establish native plan application, stereo/source/warp checks,
renderer lifetime and measured source/gaze clocks independently. The reference is
576x640 per eye at 90 Hz offered, receive-to-submit p95 strictly below 15 ms and at
least 88 unique stereo submissions/s with inherited repeated-run rules.
