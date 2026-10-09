import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import demo
import ranker


class RankerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.data = demo.fixture()
        cls.model = ranker.fit(cls.data, ranker.hash_value(cls.data), epochs=120)

    def test_actual_network_learns_and_shadow_does_not_fill_observations(self):
        model = self.model
        self.assertEqual(model["status"], "trained_unqualified")
        self.assertEqual(len(model["networks"]), 3)
        self.assertTrue(model["recommendations_enabled"])
        result = ranker.shadow(model, demo.planned_pool())
        self.assertEqual(len(result["rankings"]["neural_advisory"]), 4)
        self.assertTrue(all(r["observed_metric_value"] is None for r in result["estimates"]))
        self.assertFalse(result["hardware_dispatch"])
        self.assertFalse(result["promotion_eligible"])
        self.assertEqual(result["usage"], "synthetic_ci_only")

    def test_duplicate_rows_and_aliases_do_not_satisfy_independence(self):
        data = copy.deepcopy(self.data)
        train = [r for r in data["rows"] if r["split"] == "train"][:2]
        data["rows"] = [r for r in data["rows"] if r["split"] != "train"]
        for i in range(100):
            row = copy.deepcopy(train[i % 2])
            row["id"] = "repeat-" + str(i)
            data["rows"].append(row)
        model = ranker.fit(data, ranker.hash_value(data), epochs=10)
        self.assertEqual(model["independent_units"]["train"], 1)
        self.assertEqual(model["abstention_reason"], "insufficient_independent_units")
        result = ranker.shadow(model, demo.planned_pool())
        self.assertEqual(result["rankings"]["neural_advisory"], [])
        self.assertEqual(len(result["rankings"]["deterministic_priority"]), 4)

    def test_transitive_components_and_cross_split_leak_reject(self):
        rows = copy.deepcopy(self.data["rows"][:3])
        rows[1]["scene_id"] = rows[0]["scene_id"]
        rows[2]["session_id"] = rows[1]["session_id"]
        self.assertEqual(len(ranker.components(rows)), 1)
        data = copy.deepcopy(self.data)
        validation = next(r for r in data["rows"] if r["split"] == "validation")
        validation["correlation_id"] = data["rows"][0]["correlation_id"]
        with self.assertRaisesRegex(ValueError, "leakage"):
            ranker.validate(data)

    def test_null_labels_do_not_become_zero_or_independent_support(self):
        data = copy.deepcopy(self.data)
        for row in data["rows"]:
            if row["split"] == "test":
                row["value"] = None
        model = ranker.fit(data, ranker.hash_value(data), epochs=10)
        self.assertEqual(model["missing_observations"], 10)
        self.assertEqual(model["independent_units"]["test"], 0)
        self.assertEqual(model["status"], "abstained")

    def test_heldout_labels_do_not_change_weights_or_training_normalization(self):
        data = copy.deepcopy(self.data)
        for row in data["rows"]:
            if row["split"] == "test":
                row["value"] += 100000
        other = ranker.fit(data, ranker.hash_value(data), epochs=120)
        for key in ("feature_mean", "feature_scale", "target_mean", "target_scale", "networks"):
            self.assertEqual(self.model[key], other[key])
        self.assertEqual(self.model["recommendations_enabled"], other["recommendations_enabled"])
        self.assertGreater(other["evaluation"]["test"]["group_mean_absolute_error"], 90000)

    def test_domain_units_receipt_and_synthetic_separation(self):
        for field, value in (("unit", "ms"), ("evidence_domain", "headset_timing")):
            data = copy.deepcopy(self.data)
            data["cohort"][field] = value
            with self.assertRaises(ValueError):
                ranker.validate(data)
        data = copy.deepcopy(self.data)
        data["rows"][0]["receipt_type"] = "host_measurement"
        with self.assertRaises(ValueError):
            ranker.validate(data)
        pool = demo.planned_pool()
        pool["synthetic_fixture"] = False
        for row in pool["rows"]:
            row["receipt_type"] = "real_byte_correctness"
        with self.assertRaisesRegex(ValueError, "synthetic fixture"):
            ranker.shadow(self.model, pool)

    def test_model_abstains_on_no_signal_and_out_of_range(self):
        data = copy.deepcopy(self.data)
        for row in data["rows"]:
            row["value"] = 100
        constant = ranker.fit(data, ranker.hash_value(data), epochs=10)
        self.assertFalse(constant["recommendations_enabled"])
        pool = demo.planned_pool()
        pool["rows"][0]["features"][0] = 100
        result = ranker.shadow(self.model, pool)
        row = next(r for r in result["estimates"] if r["experiment_id"] == "planned-0")
        self.assertEqual(row["abstention_reason"], "feature_out_of_training_range")
        self.assertIsNone(row["estimated_metric_value"])

    def test_feature_leakage_nonfinite_and_cohort_mismatch_reject(self):
        for mutation in ("target_feature", "after_result", "nan", "cohort"):
            data = copy.deepcopy(self.data)
            if mutation == "target_feature":
                data["features"][0]["name"] = "serialized_bytes"
            elif mutation == "after_result":
                data["features"][0]["availability"] = "after_measurement"
            elif mutation == "nan":
                data["rows"][0]["features"][0] = float("nan")
            else:
                data["rows"][0]["cohort_sha256"] = "f" * 64
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                ranker.validate(data)

    def test_pending_dataset_rows_can_be_ranked_but_test_identities_cannot(self):
        data = copy.deepcopy(self.data)
        pool = demo.planned_pool()
        data["rows"].extend(copy.deepcopy(pool["rows"]))
        model = ranker.fit(data, ranker.hash_value(data), epochs=10)
        self.assertEqual(len(ranker.shadow(model, pool)["estimates"]), 4)
        pool["rows"][0]["scene_id"] = "test-0"
        with self.assertRaisesRegex(ValueError, "frozen"):
            ranker.shadow(model, pool)

    def test_serialized_advisory_controls_fail_closed(self):
        for key, value in (("recommendations_enabled", "false"), ("recommendations_enabled", 1),
                           ("synthetic_fixture", 1), ("usage", "production"),
                           ("independent_units", {"train": 1, "validation": 5, "test": 5})):
            model = copy.deepcopy(self.model)
            model[key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                ranker.shadow(model, demo.planned_pool())

    def test_baselines_are_deterministic_and_share_complete_pool(self):
        pool = demo.planned_pool()
        first = ranker.shadow(self.model, pool, seed=9, round_index=1)
        second = ranker.shadow(self.model, pool, seed=9, round_index=1)
        self.assertEqual(first, second)
        expected = {r["id"] for r in pool["rows"]}
        for name, order in first["rankings"].items():
            self.assertEqual(set(order), expected, name)

    def test_cli_end_to_end_and_exclusive_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = root / "dataset.json"
            pool = root / "pool.json"
            model = root / "model.json"
            output = root / "shadow.json"
            ranker.write(dataset, self.data)
            ranker.write(pool, demo.planned_pool())
            command = [sys.executable, str(Path(ranker.__file__)), "train", str(dataset),
                       "--out", str(model), "--epochs", "10"]
            reject = subprocess.run(command, capture_output=True, text=True)
            self.assertNotEqual(reject.returncode, 0)
            train = subprocess.run(command + ["--allow-synthetic-fixture"], capture_output=True, text=True)
            self.assertEqual(train.returncode, 0, train.stderr)
            inference = subprocess.run([sys.executable, str(Path(ranker.__file__)), "shadow",
                str(model), str(pool), "--out", str(output), "--allow-synthetic-fixture"],
                capture_output=True, text=True)
            self.assertEqual(inference.returncode, 0, inference.stderr)
            result = json.loads(output.read_text())
            self.assertEqual(result["model_file_sha256"], hashlib.sha256(model.read_bytes()).hexdigest())
            self.assertFalse(result["observations_collected"])
            with self.assertRaises(FileExistsError):
                ranker.write(output, {})


if __name__ == "__main__":
    unittest.main()
