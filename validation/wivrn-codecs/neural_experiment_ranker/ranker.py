#!/usr/bin/env python3
"""Advisory neural experiment ranking: one matched metric cohort per model."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import random
import re
import statistics

SCHEMA = 1
MIN_UNITS = {"train": 20, "validation": 5, "test": 5}
METRICS = {
    "offline_correctness": {"serialized_bytes": ("bytes", "min"),
                            "exact_mismatched_samples": ("samples", "min")},
    "simulation": {"modeled_receive_to_submit_p95_ms": ("ms", "min")},
    "host_timing": {"server_encode_p95_ms": ("ms", "min"),
                    "server_queue_p95_ms": ("ms", "min"),
                    "host_decode_p95_ms": ("ms", "min")},
    "headset_timing": {"headset_receive_to_submit_p95_ms": ("ms", "min"),
                       "headset_decode_p95_ms": ("ms", "min"),
                       "unique_stereo_submissions_per_s": ("Hz", "max")},
}
COHORT_KEYS = {"metric", "unit", "direction", "evidence_domain", "baseline_sha",
               "harness_sha", "comparison_config_sha256", "dataset_index_sha256",
               "device_profile_sha256", "population_sha256", "quality_contract",
               "transport_profile"}
ROW_KEYS = {"id", "candidate_sha", "cohort_sha256", "scene_id", "session_id",
            "participant_id", "correlation_id", "split", "features", "value",
            "priority", "artifacts_sha256", "receipt_type"}
IDENTITIES = ("scene_id", "session_id", "participant_id", "correlation_id")
FEATURE_FORBIDDEN = set(IDENTITIES) | {"split", "id", "candidate_sha", "value",
    "priority", "cohort_sha256"} | {m for metrics in METRICS.values() for m in metrics}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def number(value):
    return type(value) in (int, float) and math.isfinite(value)


def digest(value, length=64):
    return type(value) is str and re.fullmatch("[0-9a-f]{" + str(length) + "}", value)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def hash_value(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def load(path):
    raw = Path(path).read_bytes()
    need(len(raw) <= 16 * 1024 * 1024, "input exceeds 16 MiB")
    def unique(items):
        result = {}
        for key, value in items:
            need(key not in result, "duplicate JSON key")
            result[key] = value
        return result
    return json.loads(raw, object_pairs_hook=unique), hashlib.sha256(raw).hexdigest()


def write(path, result):
    with Path(path).open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")


def validate(data):
    need(type(data) is dict and set(data) == {
        "schema_version", "kind", "synthetic_fixture", "cohort", "features", "rows"},
        "strict ranker dataset fields required; this is not a v1 run manifest")
    need(type(data["schema_version"]) is int and data["schema_version"] == SCHEMA
         and data["kind"] == "neural_experiment_ranker_dataset", "unsupported dataset")
    need(type(data["synthetic_fixture"]) is bool, "explicit synthetic marker required")
    cohort = data["cohort"]
    need(type(cohort) is dict and set(cohort) == COHORT_KEYS, "strict matched cohort required")
    domain = cohort["evidence_domain"]
    need(domain in METRICS and cohort["metric"] in METRICS[domain],
         "metric/evidence domain mismatch; never pool domains")
    need(not data["synthetic_fixture"] or domain == "offline_correctness",
         "synthetic fixtures cannot claim hardware/simulation observations")
    need((cohort["unit"], cohort["direction"]) == METRICS[domain][cohort["metric"]],
         "metric units/direction mismatch")
    for key in COHORT_KEYS:
        if key.endswith("_sha256"):
            need(digest(cohort[key]), "invalid cohort SHA-256: " + key)
        elif key.endswith("_sha"):
            need(digest(cohort[key], 40), "exact cohort commit required: " + key)
        else:
            need(type(cohort[key]) is str and 0 < len(cohort[key]) <= 256,
                 "nonempty bounded cohort identity required")
    features = data["features"]
    need(type(features) is list and 1 <= len(features) <= 32, "1..32 feature definitions required")
    names = set()
    for feature in features:
        need(type(feature) is dict and set(feature) == {"name", "definition", "availability"},
             "strict feature definition required")
        name = feature["name"]
        need(type(name) is str and re.fullmatch("[a-z][a-z0-9_]{0,63}", name)
             and name not in names and name not in FEATURE_FORBIDDEN, "invalid/leaking feature name")
        need(feature["availability"] == "before_measurement", "post-result features forbidden")
        need(type(feature["definition"]) is str and 0 < len(feature["definition"]) <= 1024,
             "feature semantics required")
        names.add(name)
    rows = data["rows"]
    need(type(rows) is list and 0 < len(rows) <= 10000, "1..10000 rows required")
    ids, split_by_identity = set(), {}
    for row in rows:
        need(type(row) is dict and set(row) == ROW_KEYS, "strict row fields required")
        need(type(row["id"]) is str and 0 < len(row["id"]) <= 256 and row["id"] not in ids,
             "unique bounded row ID required")
        ids.add(row["id"])
        need(digest(row["candidate_sha"], 40), "exact candidate SHA required")
        need(row["cohort_sha256"] == hash_value(cohort), "row from incompatible cohort")
        need(row["split"] in ("train", "validation", "test", "planned"), "invalid split")
        for key in IDENTITIES:
            value = row[key]
            need(type(value) is str and 0 < len(value) <= 256, "explicit group identity required")
            identity = (key, value)
            need(identity not in split_by_identity or split_by_identity[identity] == row["split"],
                 "scene/session/participant/correlation leakage across splits")
            split_by_identity[identity] = row["split"]
        need(type(row["features"]) is list and len(row["features"]) == len(features)
             and all(number(x) for x in row["features"]), "finite feature vector required")
        need(row["value"] is None or number(row["value"]) and row["value"] >= 0,
             "observation must be nonnegative finite or null")
        need(row["split"] != "planned" or row["value"] is None, "planned result must remain null")
        need(type(row["priority"]) is int and 0 <= row["priority"] <= 100, "priority must be 0..100")
        artifacts = row["artifacts_sha256"]
        need(type(artifacts) is dict and all(type(k) is str and k and digest(v)
             for k, v in artifacts.items()), "invalid artifact hashes")
        if row["value"] is not None:
            need("observation" in artifacts and "input" in artifacts,
                 "observed rows require observation/input provenance")
        expected_receipt = {"offline_correctness": "real_byte_correctness",
                            "simulation": "simulation", "host_timing": "host_measurement",
                            "headset_timing": "device_capture"}[domain]
        if data["synthetic_fixture"]:
            expected_receipt = "synthetic_fixture"
        need(row["receipt_type"] == expected_receipt, "receipt type/evidence domain mismatch")
        if domain == "headset_timing" and row["value"] is not None:
            need({"device_capture", "clock", "configuration", "population"} <= artifacts.keys(),
                 "headset labels require explicit device/clock/configuration/population receipt hashes")
    return data


def components(rows):
    """Join all correlations transitively, even through rows with missing labels."""
    parent = list(range(len(rows)))
    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    seen = {}
    for i, row in enumerate(rows):
        for key in IDENTITIES:
            identity = (key, row[key])
            if identity in seen:
                parent[find(i)] = find(seen[identity])
            seen[identity] = i
    groups = defaultdict(list)
    for i, row in enumerate(rows):
        groups[find(i)].append(row)
    return list(groups.values())


def predict_net(net, x):
    hidden = [math.tanh(b + sum(w * v for w, v in zip(weights, x)))
              for weights, b in zip(net["w1"], net["b1"])]
    return sum(w * h for w, h in zip(net["w2"], hidden)) + net["b2"], hidden


def train_net(groups, dimensions, seed, epochs):
    rng = random.Random(seed)
    net = {"w1": [[rng.gauss(0, 1 / math.sqrt(dimensions)) for _ in range(dimensions)]
                  for _ in range(8)],
           "b1": [0.0] * 8, "w2": [rng.gauss(0, .2) for _ in range(8)], "b2": 0.0}
    bootstrap = [rng.choice(groups) for _ in groups]
    for epoch in range(epochs):
        rng.shuffle(bootstrap)
        rate = .03 / math.sqrt(1 + epoch / 100)
        for group in bootstrap:
            g1 = [[0.0] * dimensions for _ in range(8)]
            gb1, g2, gb2 = [0.0] * 8, [0.0] * 8, 0.0
            for x, target in group:
                prediction, hidden = predict_net(net, x)
                error = 2 * max(-5, min(5, prediction - target)) / len(group)
                gb2 += error
                for j in range(8):
                    g2[j] += error * hidden[j]
                    back = error * net["w2"][j] * (1 - hidden[j] ** 2)
                    gb1[j] += back
                    for k in range(dimensions):
                        g1[j][k] += back * x[k]
            net["b2"] -= rate * gb2
            for j in range(8):
                net["w2"][j] -= rate * g2[j]
                net["b1"][j] -= rate * gb1[j]
                for k in range(dimensions):
                    net["w1"][j][k] -= rate * g1[j][k]
    return net


def estimate(model, features):
    x = [(v - m) / s for v, m, s in zip(features, model["feature_mean"], model["feature_scale"])]
    values = [predict_net(net, x)[0] * model["target_scale"] + model["target_mean"]
              for net in model["networks"]]
    return statistics.mean(values), statistics.stdev(values)


def fit(data, dataset_sha256, seed=20261009, epochs=240):
    validate(data)
    need(digest(dataset_sha256), "dataset file hash required")
    need(type(seed) is int and type(epochs) is int and 10 <= epochs <= 1000,
         "integer seed and 10..1000 epochs required")
    groups = components(data["rows"])
    observed = {split: [[r for r in group if r["value"] is not None]
                       for group in groups if group[0]["split"] == split]
                for split in MIN_UNITS}
    observed = {split: [group for group in values if group] for split, values in observed.items()}
    result = {"schema_version": SCHEMA, "kind": "neural_experiment_ranker_model",
              "status": "abstained", "synthetic_fixture": data["synthetic_fixture"],
              "usage": "synthetic_ci_only" if data["synthetic_fixture"] else "advisory_shadow_only",
              "cohort": data["cohort"], "cohort_sha256": hash_value(data["cohort"]),
              "features": data["features"], "dataset_sha256": dataset_sha256,
              "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "seed": seed, "epochs": epochs, "minimum_independent_units": MIN_UNITS,
              "independent_units": {k: len(v) for k, v in observed.items()},
              "missing_observations": sum(r["value"] is None and r["split"] != "planned" for r in data["rows"]),
              "training_identities": {k: sorted({r[k] for r in data["rows"] if r["split"] != "planned"})
                                      for k in IDENTITIES},
              "lookup": {}, "promotion_eligible": False, "hardware_dispatch": False,
              "provenance_verification": "hash declarations only; artifacts not authenticated or replayed",
              "evidence_warning": "Rows, candidate aliases and shared baseline repeats are not independent units."}
    if any(len(observed[k]) < MIN_UNITS[k] for k in MIN_UNITS):
        result["abstention_reason"] = "insufficient_independent_units"
        return result
    training = [r for group in observed["train"] for r in group]
    columns = list(zip(*(r["features"] for r in training)))
    mean = [statistics.mean(statistics.mean(r["features"][k] for r in group)
                            for group in observed["train"]) for k in range(len(columns))]
    scale = [max(math.sqrt(statistics.mean(statistics.mean((r["features"][k] - mean[k]) ** 2
             for r in group) for group in observed["train"])), 1e-9) for k in range(len(columns))]
    target_mean = statistics.mean(statistics.mean(r["value"] for r in g) for g in observed["train"])
    target_scale = max(math.sqrt(statistics.mean(statistics.mean((r["value"] - target_mean) ** 2
                       for r in g) for g in observed["train"])), 1e-9)
    normalized = [[([(v - m) / s for v, m, s in zip(r["features"], mean, scale)],
                    (r["value"] - target_mean) / target_scale) for r in group]
                  for group in observed["train"]]
    result.update(status="trained_unqualified", feature_mean=mean, feature_scale=scale,
                  feature_min=[min(c) for c in columns], feature_max=[max(c) for c in columns],
                  target_mean=target_mean, target_scale=target_scale,
                  networks=[train_net(normalized, len(mean), seed + i, epochs) for i in range(3)])
    by_candidate = defaultdict(list)
    for group in observed["train"]:
        by_arm = defaultdict(list)
        for row in group:
            by_arm[row["candidate_sha"]].append(row["value"])
        for candidate, values in by_arm.items():
            by_candidate[candidate].append(statistics.mean(values))
    result["lookup"] = {c: statistics.mean(values) for c, values in by_candidate.items()}
    result["evaluation"] = {}
    validation_errors = []
    for split in ("validation", "test"):
        model_errors, baseline_errors = [], []
        for group in observed[split]:
            errors = [abs(estimate(result, r["features"])[0] - r["value"]) for r in group]
            model_errors.append(statistics.mean(errors))
            baseline_errors.append(statistics.mean(abs(target_mean - r["value"]) for r in group))
        result["evaluation"][split] = {"group_mean_absolute_error": statistics.mean(model_errors),
                                     "constant_training_mean_mae": statistics.mean(baseline_errors),
                                     "independent_units": len(model_errors)}
        if split == "validation":
            validation_errors = model_errors
    # Heuristic, not calibrated coverage; held-out test never controls this gate.
    result["heuristic_validation_error_p90"] = sorted(validation_errors)[math.ceil(.9 * len(validation_errors)) - 1]
    ev = result["evaluation"]["validation"]
    result["recommendations_enabled"] = ev["group_mean_absolute_error"] < .98 * ev["constant_training_mean_mae"]
    result["uncertainty_kind"] = "uncalibrated_ensemble_spread_and_validation_group_error"
    if not result["recommendations_enabled"]:
        result["abstention_reason"] = "validation_did_not_improve_constant_mean_by_two_percent"
    return result


def validate_model(model):
    need(type(model) is dict and model.get("kind") == "neural_experiment_ranker_model"
         and type(model.get("schema_version")) is int and model["schema_version"] == SCHEMA,
         "unsupported model")
    need(model.get("cohort_sha256") == hash_value(model["cohort"])
         and model.get("promotion_eligible") is False and model.get("hardware_dispatch") is False,
         "invalid advisory model controls")
    need(model.get("status") in ("abstained", "trained_unqualified"), "invalid model status")
    need(type(model.get("synthetic_fixture")) is bool and model.get("usage") ==
         ("synthetic_ci_only" if model["synthetic_fixture"] else "advisory_shadow_only"),
         "invalid fixture/advisory usage controls")
    need(model.get("minimum_independent_units") == MIN_UNITS
         and type(model.get("independent_units")) is dict and set(model["independent_units"]) == set(MIN_UNITS)
         and all(type(v) is int and v >= 0 for v in model["independent_units"].values()),
         "invalid independent support controls")
    need(type(model.get("lookup")) is dict and all(digest(k, 40) and number(v) and v >= 0
         for k, v in model["lookup"].items()), "invalid training lookup")
    need(type(model.get("training_identities")) is dict and set(model["training_identities"]) == set(IDENTITIES)
         and all(type(values) is list and all(type(v) is str and v for v in values)
                 for values in model["training_identities"].values()), "invalid frozen identities")
    if model["status"] == "abstained":
        return
    need(type(model.get("recommendations_enabled")) is bool,
         "recommendations_enabled must be an exact boolean")
    need(all(model["independent_units"][k] >= MIN_UNITS[k] for k in MIN_UNITS),
         "trained model lacks minimum independent support")
    n = len(model["features"])
    need(1 <= n <= 32 and len(model["networks"]) == 3, "invalid model shape")
    for key in ("feature_mean", "feature_scale", "feature_min", "feature_max"):
        need(type(model[key]) is list and len(model[key]) == n
             and all(number(x) for x in model[key]), "invalid normalization")
    need(all(x > 0 for x in model["feature_scale"]) and number(model["target_scale"])
         and model["target_scale"] > 0 and number(model["target_mean"])
         and number(model["heuristic_validation_error_p90"])
         and model["heuristic_validation_error_p90"] >= 0, "invalid model scale/error")
    for net in model["networks"]:
        need(set(net) == {"w1", "b1", "w2", "b2"} and len(net["w1"]) == 8
             and len(net["b1"]) == len(net["w2"]) == 8
             and all(len(row) == n and all(number(x) for x in row) for row in net["w1"])
             and all(number(x) for x in net["b1"] + net["w2"]) and number(net["b2"]),
             "invalid neural weights")


def shadow(model, pool, seed=20261009, round_index=0):
    validate_model(model)
    validate(pool)
    need(type(seed) is int and type(round_index) is int and round_index >= 0, "invalid baseline seed/index")
    need(pool["cohort"] == model["cohort"] and pool["features"] == model["features"],
         "inference cohort or feature semantics differ")
    need(pool["synthetic_fixture"] is model["synthetic_fixture"],
         "synthetic fixture model cannot enter research inference")
    need(all(r["split"] == "planned" and r["value"] is None for r in pool["rows"]),
         "shadow pool must contain only unobserved planned experiments")
    for row in pool["rows"]:
        need(all(row[k] not in model["training_identities"][k] for k in IDENTITIES),
             "shadow pool reuses frozen training/validation/test identities")
    priority = sorted(pool["rows"], key=lambda r: (-r["priority"], r["id"]))
    random_rows = list(priority)
    random.Random(seed).shuffle(random_rows)
    arms = defaultdict(list)
    for row in priority:
        arms[row["candidate_sha"]].append(row)
    names = sorted(arms)
    offset = round_index % len(names)
    names = names[offset:] + names[:offset]
    round_robin = []
    while any(arms.values()):
        for name in names:
            if arms[name]:
                round_robin.append(arms[name].pop(0))
    sign = 1 if pool["cohort"]["direction"] == "min" else -1
    lookup = model["lookup"]
    lookup_rows = sorted(priority, key=lambda r: (r["candidate_sha"] not in lookup,
                          sign * lookup.get(r["candidate_sha"], 0), -r["priority"], r["id"]))
    estimates = []
    for row in priority:
        reason = None
        value = spread = width = None
        if model["status"] == "abstained":
            reason = model.get("abstention_reason", "model_abstained")
        elif not model["recommendations_enabled"]:
            reason = model.get("abstention_reason", "validation_gate")
        elif any(v < low or v > high for v, low, high in
                 zip(row["features"], model["feature_min"], model["feature_max"])):
            reason = "feature_out_of_training_range"
        else:
            value, spread = estimate(model, row["features"])
            width = max(2 * spread, model["heuristic_validation_error_p90"])
            if not all(number(x) for x in (value, spread, width)) or value < 0:
                reason, value, spread, width = "invalid_metric_estimate", None, None, None
        estimates.append({"experiment_id": row["id"], "candidate_sha": row["candidate_sha"],
                          "observed_metric_value": None, "estimated_metric_value": value,
                          "ensemble_standard_deviation": spread, "heuristic_half_width": width,
                          "abstention_reason": reason})
    neural_order = [e["experiment_id"] for e in sorted(
        (e for e in estimates if e["abstention_reason"] is None),
        key=lambda e: (sign * e["estimated_metric_value"], e["experiment_id"]))]
    return {"schema_version": SCHEMA, "kind": "neural_experiment_ranker_shadow",
            "usage": model["usage"], "cohort": pool["cohort"],
            "model_sha256": hash_value(model), "pool_sha256": hash_value(pool),
            "observations_collected": False, "hardware_dispatch": False, "promotion_eligible": False,
            "provenance_verification": "input hash declarations only; no new measurements",
            "independent_units": model["independent_units"],
            "baseline_policy": "Same planned pool; controls are orders only, never measured experiment outcomes.",
            "estimates": estimates, "rankings": {
                "neural_advisory": neural_order,
                "deterministic_priority": [r["id"] for r in priority],
                "seeded_random": [r["id"] for r in random_rows],
                "candidate_round_robin": [r["id"] for r in round_robin],
                "exact_candidate_training_lookup": [r["id"] for r in lookup_rows]},
            "seed": seed, "round_index": round_index,
            "uncertainty": "Heuristic only; no calibrated coverage, headset pass probability or codec selection.",
            "next_action": "Human reviews suggested experiments; this tool never launches them."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    train = commands.add_parser("train")
    train.add_argument("dataset")
    train.add_argument("--out", required=True)
    train.add_argument("--seed", type=int, default=20261009)
    train.add_argument("--epochs", type=int, default=240)
    train.add_argument("--allow-synthetic-fixture", action="store_true")
    infer = commands.add_parser("shadow")
    infer.add_argument("model")
    infer.add_argument("pool")
    infer.add_argument("--out", required=True)
    infer.add_argument("--seed", type=int, default=20261009)
    infer.add_argument("--round-index", type=int, default=0)
    infer.add_argument("--allow-synthetic-fixture", action="store_true")
    args = parser.parse_args()
    try:
        if args.command == "train":
            data, digest_ = load(args.dataset)
            validate(data)
            need(not data["synthetic_fixture"] or args.allow_synthetic_fixture,
                 "synthetic fixture requires explicit CI/test opt-in")
            result = fit(data, digest_, args.seed, args.epochs)
        else:
            model, model_file_hash = load(args.model)
            pool, pool_file_hash = load(args.pool)
            need(not model.get("synthetic_fixture") or args.allow_synthetic_fixture,
                 "synthetic fixture requires explicit CI/test opt-in")
            result = shadow(model, pool, args.seed, args.round_index)
            result.update(model_file_sha256=model_file_hash, pool_file_sha256=pool_file_hash)
        write(args.out, result)
        print(json.dumps({"status": result.get("status", "shadow_only"), "out": args.out}))
    except (ValueError, KeyError, TypeError, OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
