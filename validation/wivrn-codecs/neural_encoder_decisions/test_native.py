import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import core
import native

# This adapter is a fixture container, not RRF/RRB or a compression result.
ADAPTER = '''
import json
from pathlib import Path
import sys
stage, source, output = sys.argv[1:4]
data = Path(source).read_bytes()
if stage == "encode":
    plan = json.loads(Path(sys.argv[4]).read_text())
    assert plan["blocks"][0]["mode"] in ("raw", "plain", "up", "adaptive_bytes")
    Path(output).write_bytes(b"fixture:" + data)
else:
    data = data[len(b"fixture:"):]
    if stage == "bad-decode":
        data = bytes([data[0] ^ 1]) + data[1:]
    Path(output).write_bytes(data)
'''


@unittest.skipUnless(os.name == "posix", "native adapter runner requires POSIX")
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.script = self.root / "fixture_adapter.py"
        self.script.write_text(ADAPTER)
        executable = Path(sys.executable).resolve()
        def record(path):
            return {"path": str(path), "sha256": core.sha256(path.read_bytes())}
        inputs = {}
        for eye, value in (("left", 11), ("right", 22)):
            path = self.root / (eye + ".rgb")
            path.write_bytes(bytes([value]) * 24)
            inputs[eye] = record(path)
        self.config = {
            "schema_version": 1, "profile": core.PROFILE, "quality_contract": "lossless",
            "provenance": {"candidate_commit": "1" * 40, "baseline_commit": "2" * 40,
                           "harness_commit": "3" * 40, "dataset_index_sha256": "4" * 64},
            "identity": {"stereo_pair_id": "pair", "source_frame_id": "source", "warp_id": "warp"},
            "width": 4, "height": 2, "inputs": inputs, "policy": "fixed_up", "model": None,
            "encoder_argv": [str(executable), str(self.script), "encode", "{input}", "{output}", "{plan}"],
            "decoder_argv": [str(executable), str(self.script), "decode", "{input}", "{output}"],
            "artifacts": [record(executable), record(self.script)],
            "payload_cap_bytes": 1000, "timeout_seconds": 5.0}

    def tearDown(self):
        self.temp.cleanup()

    def run_config(self, config=None, name="run"):
        path = self.root / (name + ".json")
        core.write_json(path, self.config if config is None else config)
        output = self.root / name
        code = native.run(path, output)
        return code, core.read_json(output / "decision-receipt.json")

    def test_separate_process_exact_bytes_and_no_hardware_claim(self):
        code, receipt = self.run_config()
        self.assertEqual(code, 0)
        self.assertEqual(receipt["encoded_eye_payload_bytes"], 64)
        self.assertTrue(receipt["exact_reference_pixels"])
        self.assertTrue(receipt["source_plan_match"])
        self.assertEqual(receipt["admitted_eyes"], ["left", "right"])
        self.assertFalse(receipt["decision_application_verified"])
        self.assertIsNone(receipt["wire_bytes"])
        pair = core.read_json(self.root / "run" / "pair" / "receipt.json")
        self.assertEqual(pair["eyes"]["left"]["commands"]["decode"]["returncode"], 0)

    def test_dense_cap_reject_counts_both_eyes(self):
        self.config["payload_cap_bytes"] = 63
        code, receipt = self.run_config()
        self.assertEqual(code, 2)
        self.assertEqual(receipt["over_cap_pairs"], 1)
        self.assertEqual(receipt["rejected_pairs"], 1)
        self.assertEqual(receipt["offered_pairs"], 1)
        self.assertEqual(receipt["admitted_eyes"], [])
        self.assertEqual(receipt["encoded_eye_payload_bytes"], 64)
        self.assertTrue(receipt["exact_reference_pixels"])

    def test_quality_failure_cannot_be_admitted(self):
        self.config["decoder_argv"][2] = "bad-decode"
        code, receipt = self.run_config()
        self.assertEqual(code, 1)
        self.assertEqual(receipt["status"], "failed")
        self.assertFalse(receipt["exact_reference_pixels"])
        self.assertEqual(receipt["admitted_eyes"], [])

    def test_hash_mismatch_prevents_command_execution(self):
        self.config["artifacts"][1]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            self.run_config()
        self.assertFalse((self.root / "run").exists())

    def test_existing_output_rejected_and_no_plan_to_decoder(self):
        self.run_config()
        with self.assertRaises(FileExistsError):
            native.run(self.root / "run.json", self.root / "run")
        config = copy.deepcopy(self.config)
        config["decoder_argv"].append("{plan}")
        with self.assertRaisesRegex(ValueError, "must not receive"):
            self.run_config(config, "bad")


if __name__ == "__main__":
    unittest.main()
