"""Synthetic validation fixtures, not measured codec evidence."""
import json
import tempfile
import unittest
from pathlib import Path

from manifest import InvalidManifest, load, validate


EXAMPLE = Path(__file__).with_name("example-run.json")


def completed():
    record = json.loads(EXAMPLE.read_text(encoding="utf-8"))
    record["status"] = "completed"
    record["provenance"]["configuration_sha256"] = "a" * 64
    record["provenance"]["artifacts_sha256"] = {"encoded_streams": "b" * 64}
    record["dataset"] = {"sha256": "c" * 64, "splits": {
        "train": [{"scene": "train-scene", "session": "train-session"}],
        "validation": [], "test": [{"scene": "test-scene", "session": "test-session"}]}}
    record["observations"] = {
        "offered_pairs": 2, "rejected_pairs": 0, "fallback_pairs": 0, "over_cap_pairs": 0,
        "serialized_bytes": 240, "wire_bytes": None, "max_stereo_serialized_bytes": 120,
        "server_encode_p95_ms": None, "server_queue_p95_ms": None,
        "headset_decode_p95_ms": None, "headset_receive_to_submit_p95_ms": None,
        "unique_stereo_submissions_per_s": None, "source_age_p95_ms": None,
        "source_age_clock_basis": None, "stereo_identity_verified": True,
        "population": {"definition": "Two synthetic test pairs", "window_s": None,
                       "server_pairs": None, "headset_pairs": None},
        "quality": {"metric": "exact_mismatched_samples", "value": 0, "reference": "synthetic Q8"}}
    return record


class ManifestTests(unittest.TestCase):
    def test_planned_has_no_invented_measurements(self):
        self.assertIsNone(load(EXAMPLE)["observations"])

    def test_unknowns_are_distinct_from_zero(self):
        record = completed()
        validate(record)
        self.assertIsNone(record["observations"]["wire_bytes"])
        self.assertEqual(record["observations"]["rejected_pairs"], 0)

    def test_invalid_provenance_metrics_and_claims(self):
        changes = [
            (("provenance", "candidate_commit"), "research/moving-head"),
            (("provenance", "baseline_commit"), "abc123"),
            (("provenance", "harness_commit"), None),
            (("provenance", "configuration_sha256"), None),
            (("provenance", "artifacts_sha256"), {}),
            (("observations", "serialized_bytes"), None),
            (("observations", "wire_bytes"), -1),
            (("observations", "server_encode_p95_ms"), float("nan")),
            (("observations", "headset_decode_p95_ms"), 1),
            (("observations", "source_age_p95_ms"), 5),
            (("observations", "stereo_identity_verified"), None),
            (("candidate", "learned"), True),
            (("promoted",), True)]
        for path, value in changes:
            with self.subTest(path=path):
                record = completed()
                target = record
                for key in path[:-1]:
                    target = target[key]
                target[path[-1]] = value
                with self.assertRaises(InvalidManifest):
                    validate(record)

    def test_no_scene_or_session_leakage(self):
        for key in ("scene", "session"):
            record = completed()
            record["dataset"]["splits"]["test"][0][key] = record["dataset"]["splits"]["train"][0][key]
            with self.assertRaises(InvalidManifest):
                validate(record)

    def test_simulation_cannot_be_hardware(self):
        record = completed()
        record.update(evidence="simulation", execution="simulation")
        validate(record)
        record["execution"] = "hardware"
        with self.assertRaises(InvalidManifest):
            validate(record)
        record.update(evidence="headset_timing")
        record["transport"]["mode"] = "datagram_simulation"
        with self.assertRaises(InvalidManifest):
            validate(record)

    def test_cap_rejection_is_counted_and_never_qualifies(self):
        record = completed()
        record["transport"]["application_cap_bytes"] = 100
        with self.assertRaises(InvalidManifest):
            validate(record)
        record["observations"].update(over_cap_pairs=2, rejected_pairs=2)
        validate(record)

    def test_completed_does_not_assert_correctness(self):
        record = completed()
        record["observations"]["stereo_identity_verified"] = False
        record["observations"]["quality"]["value"] = 12
        validate(record)

    def test_json_nonfinite_and_duplicate_keys_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            for content in ('{"schema_version": 1, "schema_version": 1}', '{"metric": NaN}'):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(InvalidManifest):
                    load(path)


if __name__ == "__main__":
    unittest.main()
