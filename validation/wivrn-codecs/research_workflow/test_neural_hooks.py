"""Synthetic fixtures only: no headset, network, gaze API or latency qualification."""
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile
import unittest
from contextlib import redirect_stdout, redirect_stderr
from unittest.mock import patch

import neural_hooks as hooks


def sha(data):
    return hashlib.sha256(data).hexdigest()


class HooksTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.capture_root = self.root / "synthetic-capture"
        self.capture_root.mkdir()
        pin = {"repository": "synthetic/fixture", "ref": "fixture-only", "commit": "1" * 40}
        self.plan = {
            "schema": hooks.PLAN, "run_id": "synthetic-fixture-only",
            "evidence": "synthetic_fixture",
            "identity": {name: dict(pin) for name in ("candidate", "baseline", "harness")},
            "learned": True, "inputs": {},
            "quality": {"mode": "new_lossy", "reference": "synthetic-fixture-only"},
            "route": {"profile_id": "synthetic-fixture", "protocol": "none",
                      "path": "simulated", "measurement_layer": "unmeasured"},
            "setup_declared": {"description": "synthetic; no physical device"},
            "gaze": {"arm": "none"}, "adapter_argv": None,
            "limits": {"timeout_seconds": 2, "max_artifact_bytes": 1000000,
                       "max_total_bytes": 4000000, "max_rows": 100},
            "stereo_payload_cap_bytes": 4}
        for name in ("config", "dataset", "model"):
            path = self.root / (name + ".json")
            data = json.dumps({"synthetic_fixture": name}).encode()
            path.write_bytes(data)
            self.plan["inputs"][name] = {"path": str(path), "sha256": sha(data)}
        self.plan_path = self.root / "plan.json"
        self.offered = [
            {"pair_id": name, "source_id": "source-" + name, "warp_id": "warp",
             "target_id": "target-" + name} for name in ("p1", "p2")]
        self.pairs = [
            dict(self.offered[0], disposition="candidate", reason="within cap",
                 candidate_artifact="one.bin", candidate_envelope_bytes=3, over_cap=False),
            dict(self.offered[1], disposition="rejected", reason="dense over cap",
                 candidate_artifact="dense.bin", candidate_envelope_bytes=5, over_cap=True)]
        self.events = [
            dict(self.offered[0], event_id="receipt-1", kind="receipt",
                 clock="synthetic-clock", timestamp_ns=1000000000),
            dict(self.offered[0], event_id="submit-1", kind="xr_end_frame_return",
                 clock="synthetic-clock", timestamp_ns=1010000000,
                 post_call=True, success=True, xr_result="XR_SUCCESS", presentation_target_id="actual-target",
                 eyes={eye: dict(self.offered[0], presentation_target_id="actual-target") for eye in ("left", "right")})]
        self.extra = {}
        self.capture = {
            "schema": hooks.CAPTURE, "run_id": self.plan["run_id"], "status": "completed",
            "evidence": "synthetic_fixture",
            "runtime": {"device_role": "synthetic_fixture"},
            "capabilities": ["stereo_receipt_events", "post_successful_xr_end_frame_events"],
            "client_clock": {"id": "synthetic-clock", "unit": "ns", "kind": "synthetic"},
            "receipt_scope": "earliest_application_receipt_any_eye",
            "window": {"clock": "synthetic-clock", "start_ns": 0, "end_ns": 2000000000},
            "coverage": {"render_tail_complete": False, "notes": "synthetic unknown tail"},
            "offered_pairs": 2}
        self.write()

    def write(self):
        self.plan_path.write_text(json.dumps(self.plan))
        files = {
            "offered.jsonl": ("offered", self.offered),
            "pairs.jsonl": ("pairs", self.pairs),
            "events.jsonl": ("native_events", self.events),
            "runtime.txt": ("runtime", b"synthetic fixture; no hardware\n"),
            "quality.json": ("quality", b'{"synthetic_fixture":true,"measured_quality":null}'),
            "one.bin": ("candidate_bitstream", b"123"),
            "dense.bin": ("candidate_bitstream", b"dense"),
            **self.extra}
        artifacts = {}
        for name, (role, content) in files.items():
            if isinstance(content, list):
                content = b"".join((json.dumps(row) + "\n").encode() for row in content)
            (self.capture_root / name).write_bytes(content)
            artifacts[name] = {"role": role, "bytes": len(content), "sha256": sha(content)}
        self.capture.update(identity=copy.deepcopy(self.plan["identity"]),
                            route=copy.deepcopy(self.plan["route"]),
                            input_sha256={k: v["sha256"] for k, v in self.plan["inputs"].items()},
                            plan_sha256=sha(self.plan_path.read_bytes()), artifacts=artifacts)
        self.capture["evidence"] = self.plan["evidence"]
        (self.capture_root / "capture.json").write_text(json.dumps(self.capture))

    def collect(self):
        return hooks.collect(self.plan_path, self.capture_root)

    def failed(self):
        result = self.collect()
        self.assertEqual(result["status"], "failed", result)
        self.assertIsNone(result["metrics"])
        return result

    def test_synthetic_roundtrip_preserves_dense_rejection(self):
        result = self.collect()
        self.assertEqual(result["status"], "capture_validated")
        self.assertFalse(result["independent_hardware_verification"])
        m = result["metrics"]
        self.assertEqual((m["offered_pairs"], m["rejected_pairs"], m["over_cap_pairs"]), (2, 1, 1))
        self.assertEqual(m["candidate_serialized_bytes"], 8)
        self.assertEqual(m["diagnostic_receive_to_submit_p95_ms"], 10)
        self.assertIsNone(m["headset_receive_to_submit_p95_ms"])
        self.assertEqual(m["unique_stereo_submissions_per_s"], .5)
        self.assertFalse(result["coverage"]["render_tail_complete"])

    def test_missing_adapter_blocks_without_launch(self):
        with patch.object(hooks.subprocess, "Popen") as launch:
            result = hooks.collect(self.plan_path, self.capture_root, True)
        self.assertEqual(result["status"], "blocked")
        launch.assert_not_called()

    def test_missing_native_event_capability_blocks(self):
        self.capture["capabilities"] = []
        self.write()
        self.assertEqual(self.collect()["status"], "blocked")

    def test_offline_or_relabelled_receipt_cannot_supply_headset_metrics(self):
        self.capture["schema"] = "wivrn-offline-stereo-receipt-v1"
        self.write()
        self.failed()
        self.capture["schema"] = hooks.CAPTURE
        self.plan["evidence"] = "native_headset"
        self.write()
        self.failed()  # Synthetic clock/runtime cannot masquerade as a headset.

    def test_input_and_candidate_pins_are_bound(self):
        (self.root / "model.json").write_text("changed")
        self.failed()
        (self.root / "model.json").write_text(json.dumps({"synthetic_fixture": "model"}))
        self.capture["identity"]["candidate"]["commit"] = "2" * 40
        (self.capture_root / "capture.json").write_text(json.dumps(self.capture))
        self.failed()

    def test_population_duplicate_and_missing_pair_rejected(self):
        self.pairs.pop()
        self.write()
        self.failed()
        self.pairs.append(dict(self.pairs[0]))
        self.write()
        self.failed()

    def test_dense_pair_cannot_be_admitted_or_hide_bytes(self):
        self.pairs[1]["disposition"] = "candidate"
        self.write()
        self.failed()
        self.pairs[1]["disposition"] = "rejected"
        self.pairs[1]["candidate_envelope_bytes"] = 4
        self.write()
        self.failed()

    def test_mixed_stereo_or_pre_call_event_rejected(self):
        self.events[1]["eyes"]["right"]["source_id"] = "different"
        self.write()
        self.failed()
        self.events[1]["eyes"]["right"] = dict(self.offered[0], presentation_target_id="actual-target")
        self.events[1]["post_call"] = False
        self.write()
        self.failed()

    def test_wrong_clock_and_negative_latency_rejected(self):
        self.events[1]["clock"] = "host-clock"
        self.write()
        self.failed()
        self.events[1]["clock"] = "synthetic-clock"
        self.events[1]["timestamp_ns"] = 999999999
        self.write()
        self.failed()

    def test_repeated_prior_submission_does_not_become_new(self):
        self.capture["window"]["start_ns"] = 1100000000
        self.events.append(dict(self.events[1], event_id="repeat", timestamp_ns=1200000000))
        self.write()
        result = self.collect()["metrics"]
        self.assertEqual(result["unique_stereo_submissions"], 0)
        self.assertEqual(result["repeat_submissions_in_window"], 1)
        self.assertIsNone(result["diagnostic_receive_to_submit_p95_ms"])

    def test_final_rehash_catches_later_artifact_change(self):
        original = hooks.rows
        count = 0
        def mutate(*args):
            nonlocal count
            count += 1
            if count == 1:
                (self.capture_root / "one.bin").write_bytes(b"xxx")
            return original(*args)
        with patch.object(hooks, "rows", side_effect=mutate):
            self.failed()

    def test_symlink_escape_and_duplicate_json_fail(self):
        target = self.capture_root / "one.bin"
        target.unlink()
        target.symlink_to(self.root / "model.json")
        self.failed()
        target.unlink()
        self.write()
        self.capture["artifacts"]["../model.json"] = self.capture["artifacts"].pop("one.bin")
        (self.capture_root / "capture.json").write_text(json.dumps(self.capture))
        self.failed()
        for text in ('{"x":1,"x":2}', '{"x":NaN}', '{"x":1e400}'):
            with self.assertRaises(ValueError):
                hooks.parse(text)

    def test_gaze_future_expression_time_allowed_invalid_eye_requires_fallback(self):
        self.plan["gaze"] = {"arm": "device_independent", "mode": "independent_predicted_gaze"}
        sample = {"valid": False, "stale": False, "sample_clock": "synthetic-clock",
                  "available_clock": "synthetic-clock",
                  "available_ns": 990000000, "acquisition_ns": None,
                  "pose_expression_ns": 3000000000, "horizon_ns": None,
                  "uncertainty_ns": None, "confidence": None, "calibration_artifact": None}
        gaze = {"pair_id": "p1", "target_id": "target-p1", "arm": "device_independent",
                "mode": "independent_predicted_gaze", "decision": "tracked", "fallback_policy": "uniform-quality",
                "decision_ns": 995000000, "decision_clock": "synthetic-clock",
                "presentation_target_ns": 3000000000, "presentation_target_clock": "synthetic-clock",
                "left": dict(sample), "right": dict(sample)}
        self.extra["geometry.json"] = ("gaze_geometry", b'{"synthetic":true}')
        self.extra["gaze.jsonl"] = ("gaze", [gaze])
        self.write()
        self.failed()
        gaze["decision"] = "fallback"
        self.write()
        self.assertEqual(self.collect()["status"], "capture_validated")
        gaze["left"]["available_ns"] = 996000000
        self.write()
        self.failed()
        gaze["left"]["available_ns"] = 990000000
        self.capture["window"]["start_ns"] = 1100000000
        self.events.append(dict(self.events[1], event_id="repeat", timestamp_ns=1200000000))
        self.extra["gaze.jsonl"] = ("gaze", [])
        self.write()
        self.failed()  # Repeated prior frames still require gaze decision coverage.

    def test_contradictory_xr_success_rejected(self):
        self.events[1]["success"] = False
        self.write()
        self.failed()

    def test_manual_argv_literal_and_no_overwrite(self):
        stub = self.root / "adapter.py"
        stub.write_text("import pathlib,shutil,sys\n"
                        "for p in pathlib.Path(sys.argv[3]).iterdir():\n"
                        " shutil.copyfile(p,pathlib.Path(sys.argv[2])/p.name)\n"
                        "assert sys.argv[4] == '; not a shell $HOME'\n")
        self.plan["adapter_argv"] = [sys.executable, str(stub), "{plan}", "{output}",
                                     str(self.capture_root), "; not a shell $HOME"]
        self.write()
        output = self.root / "new-run"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.main(["run", str(self.plan_path), "--output", str(output)]), 0)
            self.assertEqual(hooks.main(["run", str(self.plan_path), "--output", str(output)]), 1)
        receipt = json.loads((output / "capture-validation.json").read_text())
        self.assertEqual(receipt["status"], "capture_validated")
        self.assertNotIn("elapsed_ns", receipt)

    def test_timeout_retains_failure_without_latency_metric(self):
        self.plan["limits"]["timeout_seconds"] = 1
        self.plan["adapter_argv"] = [sys.executable, "-c", "import time; time.sleep(30)", "{plan}", "{output}"]
        self.write()
        output = self.root / "timeout-run"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.main(["run", str(self.plan_path), "--output", str(output)]), 2)
        result = json.loads((output / "capture-validation.json").read_text())
        self.assertEqual(result["status"], "failed")
        self.assertIn("TimeoutExpired", result["error"])
        self.assertIsNone(result["metrics"])

    def test_command_failure_retains_failed_receipt(self):
        self.plan["adapter_argv"] = [sys.executable, "-c", "raise SystemExit(7)", "{plan}", "{output}"]
        self.write()
        output = self.root / "failed-run"
        with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            self.assertEqual(hooks.main(["run", str(self.plan_path), "--output", str(output)]), 2)
        self.assertEqual(json.loads((output / "capture-validation.json").read_text())["status"], "failed")


if __name__ == "__main__":
    unittest.main()
