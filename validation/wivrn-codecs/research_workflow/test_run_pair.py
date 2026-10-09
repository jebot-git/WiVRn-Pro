"""Portable-to-POSIX adapter fixtures; no trained model or headset evidence."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_pair import parser, run_pair


STUB = """import os
from pathlib import Path
import sys
import time
source, target, mode = sys.argv[1:]
data = Path(source).read_bytes()
if mode == "fail":
    sys.exit(7)
if mode == "sleep":
    time.sleep(5)
if mode == "symlink":
    os.symlink(source, target)
    sys.exit(0)
if mode == "large":
    data = bytes(65536)
if mode == "lossy":
    data = bytes(min(value + 1, 255) for value in data)
if mode == "short":
    data = data[:-1]
if mode == "mutate_left" and Path(target).name.startswith("right."):
    Path(target).with_name("left.bitstream").write_bytes(bytes(6))
if mode == "copy;touch injected":
    assert mode == "copy;touch injected"
Path(target).write_bytes(data)
"""


class PairTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.stub = self.base / "adapter.py"
        self.stub.write_text(STUB)
        self.left, self.right = self.base / "left.rgb", self.base / "right.rgb"
        self.left.write_bytes(bytes(range(6)))
        self.right.write_bytes(bytes(range(10, 16)))

    def args(self, encoder="copy", decoder="copy", extra=()):
        def adapter(mode):
            return json.dumps([sys.executable, str(self.stub), "{input}", "{output}", mode])
        return parser().parse_args([
            "--left", str(self.left), "--right", str(self.right),
            "--width", "2", "--height", "1", "--output", str(self.base / "result"),
            "--encoder-argv", adapter(encoder), "--decoder-argv", adapter(decoder),
            *extra])

    def run_case(self, expected=0, **kwargs):
        self.assertEqual(run_pair(self.args(**kwargs)), expected)
        return json.loads((self.base / "result" / "receipt.json").read_text())

    def test_exact_roundtrip(self):
        receipt = self.run_case()
        self.assertEqual(receipt["stereo"]["encoded_payload_bytes"], 12)
        self.assertEqual(receipt["stereo"]["admitted_eyes"], ["left", "right"])
        for entry in receipt["eyes"].values():
            self.assertTrue(entry["rgb8_error"]["exact_equal"])
            self.assertEqual(entry["source"]["sha256"], entry["decode_output"]["sha256"])
            self.assertGreaterEqual(entry["commands"]["encode"]["elapsed_ns"], 0)

    def test_lossy_metrics(self):
        receipt = self.run_case(decoder="lossy")
        self.assertEqual(receipt["eyes"]["left"]["rgb8_error"],
                         {"exact_equal": False, "mean_absolute_error": 1.0, "max_absolute_error": 1})

    def test_short_decode_preserves_failure(self):
        receipt = self.run_case(expected=1, decoder="short")
        self.assertEqual(receipt["error"]["scope"], "left.decode")
        self.assertEqual(receipt["stereo"]["admitted_eyes"], [])

    def test_failed_command(self):
        receipt = self.run_case(expected=1, encoder="fail")
        self.assertEqual(receipt["eyes"]["left"]["commands"]["encode"]["returncode"], 7)
        self.assertEqual(receipt["status"], "failed")

    def test_cap_rejects_pair_without_hiding_measurements(self):
        receipt = self.run_case(expected=2, extra=["--stereo-payload-cap", "11"])
        self.assertEqual(receipt["stereo"]["reason"], "payload_cap_exceeded")
        self.assertEqual(receipt["stereo"]["encoded_payload_bytes"], 12)
        self.assertEqual(receipt["stereo"]["admitted_eyes"], [])
        self.assertTrue(all(entry["valid"] for entry in receipt["eyes"].values()))

    def test_right_decoder_cannot_invalidate_left_artifacts(self):
        receipt = self.run_case(expected=1, decoder="mutate_left")
        self.assertEqual(receipt["error"]["scope"], "left.final_artifacts")
        self.assertEqual(receipt["stereo"]["admitted_eyes"], [])
        self.assertFalse(receipt["stereo"]["admitted"])
        self.assertIn("artifact changed", receipt["error"]["message"])

    def test_refuses_overwrite(self):
        self.run_case()
        original = (self.base / "result" / "receipt.json").read_bytes()
        with self.assertRaises(FileExistsError):
            run_pair(self.args())
        self.assertEqual((self.base / "result" / "receipt.json").read_bytes(), original)

    def test_metacharacters_remain_literal(self):
        self.run_case(encoder="copy;touch injected")
        self.assertFalse((self.base / "result" / "injected").exists())

    def test_symlink_output_refused(self):
        receipt = self.run_case(expected=1, encoder="symlink")
        self.assertIn("regular file", receipt["error"]["message"])

    def test_file_size_budget(self):
        receipt = self.run_case(expected=1, encoder="large", extra=["--max-encoded-bytes", "8"])
        self.assertFalse(receipt["stereo"]["admitted"])

    def test_timeout(self):
        receipt = self.run_case(expected=1, encoder="sleep", extra=["--timeout-seconds", "0.1"])
        self.assertEqual(receipt["error"]["type"], "TimeoutExpired")

    def test_source_size_validation(self):
        self.left.write_bytes(b"bad")
        receipt = self.run_case(expected=1)
        self.assertEqual(receipt["error"]["scope"], "left.source")

    def test_path_placeholder_cannot_escape(self):
        args = self.args()
        args.encoder_argv += ["{output}/../escaped"]
        self.assertEqual(run_pair(args), 1)
        self.assertFalse((self.base / "escaped").exists())


if __name__ == "__main__":
    unittest.main()
