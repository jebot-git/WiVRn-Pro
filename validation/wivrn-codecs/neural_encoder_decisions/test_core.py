import copy
import math
from pathlib import Path
import tempfile
import unittest

import core
import smoke


class DecisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.index, self.digest = smoke.fixture(self.root / "fixture")
        self.data, self.rows = core.load_dataset(self.index, self.digest)

    def tearDown(self):
        self.temp.cleanup()

    def changed(self, mutate):
        data = copy.deepcopy(self.data)
        mutate(data)
        path = self.index.parent / "changed.json"
        core.write_json(path, data)
        return core.load_dataset(path, core.sha256(path.read_bytes()))

    def test_deterministic_training_reduces_training_loss(self):
        early = core.train(self.data, self.rows, self.digest, epochs=1)
        model = core.train(self.data, self.rows, self.digest, epochs=80)
        self.assertEqual(model, core.train(self.data, self.rows, self.digest, epochs=80))
        def loss(m):
            total = 0
            for row in self.rows:
                if row["split"] == "train":
                    label = min(range(3), key=lambda i: row["sizes"][i])
                    total -= math.log(core.forward(m, row["x"])[1][label])
            return total
        self.assertLess(loss(model), loss(early))
        self.assertEqual(core.parse_json(core.canonical(model)), model)

    def test_heldout_labels_do_not_enter_gradients(self):
        altered = copy.deepcopy(self.rows)
        for row in altered:
            if row["split"] != "train":
                row["sizes"] = list(reversed(row["sizes"]))
                row["x"] = [0.99] * 8
        first = core.train(self.data, self.rows, self.digest, epochs=3)
        second = core.train(self.data, altered, self.digest, epochs=3)
        self.assertEqual(first, second)

    def test_counterfactual_evaluation_and_causal_plan(self):
        model = core.train(self.data, self.rows, self.digest, epochs=5)
        report = core.evaluate(model, self.data, self.rows, self.digest)
        self.assertEqual(report["blocks"], 6)
        self.assertIsNone(report["over_cap_pairs"])
        self.assertIsNone(report["headset_receive_to_submit_p95_ms"])
        totals = report["selected_block_bytes"]
        self.assertTrue(all(totals["adaptive_bytes"] <= value for value in totals.values()))
        plan = core.plan(bytes([10]) * (4 * 33 * 3), 4, 33, "mlp", model)
        self.assertEqual([b["rows"] for b in plan["blocks"]], [32, 1])
        self.assertEqual(plan["source_sha256"], core.sha256(bytes([10]) * 396))
        for policy in ("fixed_raw", "fixed_plain", "fixed_up", "adaptive_bytes"):
            result = core.plan(bytes(24), 4, 2, policy)
            self.assertEqual(result["blocks"][0]["mode"], policy.replace("fixed_", ""))

    def test_scene_session_and_content_leakage_rejected(self):
        for field, value in (("scene", "train-scene"), ("session", "train-session")):
            with self.subTest(field=field):
                def mutate(data):
                    for row in data["records"]:
                        if row["split"] == "test":
                            row[field] = value
                with self.assertRaisesRegex(ValueError, "leakage"):
                    self.changed(mutate)
                (self.index.parent / "changed.json").unlink()

    def test_pins_and_stereo_population_rejected(self):
        mutations = [
            lambda d: d["provenance"].update(candidate_commit="moving-branch"),
            lambda d: d["records"].pop(),
            lambda d: d["records"][0].update(warp_id="different"),
            lambda d: d.update(evidence="headset_timing"),
        ]
        for mutate in mutations:
            with self.subTest(mutation=str(mutate)):
                with self.assertRaises(ValueError):
                    self.changed(mutate)
                (self.index.parent / "changed.json").unlink()

    def test_actual_artifact_hash_and_exact_oracle(self):
        with self.assertRaisesRegex(ValueError, "frozen index"):
            core.load_dataset(self.index, "0" * 64)
        def mutate(data):
            data["records"][0]["outcomes"]["up"]["decoded"] = data["records"][1]["source"]
        with self.assertRaisesRegex(ValueError, "exact oracle"):
            self.changed(mutate)
        path = self.index.parent / self.data["records"][0]["outcomes"]["raw"]["encoded"]["path"]
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "artifact digest"):
            core.load_dataset(self.index, self.digest)

    def test_nonfinite_model_and_changed_index_rejected(self):
        model = core.train(self.data, self.rows, self.digest, epochs=1)
        with self.assertRaises(ValueError):
            core.evaluate(model, self.data, self.rows, "0" * 64)
        bad = copy.deepcopy(model)
        bad["w1"][0][0] = float("nan")
        with self.assertRaises(ValueError):
            core.validate_model(bad)
        with self.assertRaises(ValueError):
            core.parse_json('{"x": NaN}')
        with self.assertRaises(ValueError):
            core.parse_json('{"x": 1, "x": 2}')
        with self.assertRaises(ValueError):
            core.plan(bytes(24), 4, 2, "mlp")
        with self.assertRaises(ValueError):
            core.plan(bytes(24), 4, 2, "fixed_up", model)

    def test_content_leakage_even_with_renamed_scene(self):
        # Copy both source and exact reconstructions under a held-out identity.
        source = self.data["records"][0]["source"]
        def mutate(data):
            for row in data["records"]:
                if row["split"] == "test":
                    row["source"] = source
                    for outcome in row["outcomes"].values():
                        outcome["decoded"] = source
                    break
        with self.assertRaisesRegex(ValueError, "content across splits"):
            self.changed(mutate)


if __name__ == "__main__":
    unittest.main()
