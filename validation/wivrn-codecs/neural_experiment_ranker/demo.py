#!/usr/bin/env python3
"""Tiny deterministic CI fixture. No corpus, hardware or research training."""
import argparse
import hashlib
from pathlib import Path
import ranker


def fixture():
    cohort = {"metric": "serialized_bytes", "unit": "bytes", "direction": "min",
              "evidence_domain": "offline_correctness", "baseline_sha": "1" * 40,
              "harness_sha": "2" * 40, "comparison_config_sha256": "3" * 64,
              "dataset_index_sha256": "4" * 64, "device_profile_sha256": "5" * 64,
              "population_sha256": "6" * 64, "quality_contract": "synthetic_ci_only",
              "transport_profile": "none_synthetic_ci"}
    features = [{"name": "planned_complexity", "definition": "Synthetic predeclared scalar; not real codec data.",
                 "availability": "before_measurement"},
                {"name": "planned_arm", "definition": "Synthetic numeric design coordinate known before measurement.",
                 "availability": "before_measurement"}]
    rows = []
    for split, count in (("train", 20), ("validation", 5), ("test", 5)):
        for i in range(count):
            x = i / 19 if split == "train" else (i + 1) / 6
            group = split + "-" + str(i)
            for arm in (0, 1):
                value = round(100 + 40 * x + 20 * x * x + 10 * arm)
                rows.append({"id": group + "-arm" + str(arm), "candidate_sha": str(7 + arm) * 40,
                    "cohort_sha256": ranker.hash_value(cohort),
                    "scene_id": group, "session_id": group, "participant_id": group,
                    "correlation_id": group, "split": split, "features": [x, arm],
                    "value": value, "priority": 50 - arm, "receipt_type": "synthetic_fixture",
                    "artifacts_sha256": {"input": ranker.hash_value({"fixture": [x, arm]}),
                                         "observation": ranker.hash_value({"synthetic_value": value})}})
    return {"schema_version": 1, "kind": "neural_experiment_ranker_dataset",
            "synthetic_fixture": True, "cohort": cohort, "features": features, "rows": rows}


def planned_pool():
    data = fixture()
    rows = []
    for i, (x, arm) in enumerate(((.2, 0), (.5, 1), (.8, 0), (.5, 0))):
        group = "planned-" + str(i)
        rows.append({"id": group, "candidate_sha": str(7 + arm) * 40,
            "cohort_sha256": ranker.hash_value(data["cohort"]),
            "scene_id": group, "session_id": group, "participant_id": group,
            "correlation_id": group, "split": "planned", "features": [x, arm],
            "value": None, "priority": 10 + i, "receipt_type": "synthetic_fixture",
            "artifacts_sha256": {}})
    data["rows"] = rows
    return data


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", required=True, help="New directory for synthetic CI artifacts")
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir()
    data, pool = fixture(), planned_pool()
    ranker.write(out / "dataset.json", data)
    ranker.write(out / "pool.json", pool)
    raw = (out / "dataset.json").read_bytes()
    model = ranker.fit(data, hashlib.sha256(raw).hexdigest())
    ranker.write(out / "model.json", model)
    ranker.write(out / "shadow.json", ranker.shadow(model, pool))
    print("Synthetic CI artifacts only: " + str(out))


if __name__ == "__main__":
    main()
