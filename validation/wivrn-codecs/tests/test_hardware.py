import copy
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hardware import HardwareRun, distribution, rgb_metrics, select_decoder, select_gpu, validate_frames


class HardwareEvidenceTests(unittest.TestCase):
    def test_gpu_identity_rejects_software_and_ambiguity(self):
        device = dict(index=2, vendor_id=0x8086, device_id=0x56a0, device_type=2)
        self.assertEqual(select_gpu(dict(devices=[device]), 0x8086, 0x56a0)["index"], 2)
        for rows in ([], [dict(device, device_type=4)], [device, device]):
            with self.assertRaises(ValueError): select_gpu(dict(devices=rows), 0x8086, 0x56a0)

    def test_decoder_never_falls_back_to_secure_or_software(self):
        candidate = dict(name="hardware", hardware=True, software=False, alias=False,
                         size_1152x1920=True, size_rate_1152x1920_90=True, low_latency=False)
        self.assertEqual(select_decoder(dict(codecs=[candidate]))["name"], "hardware")
        for bad in (dict(candidate, name="hardware.secure"), dict(candidate, software=True),
                    dict(candidate, hardware=False), dict(candidate, size_1152x1920=False)):
            with self.assertRaises(ValueError): select_decoder(dict(codecs=[bad]))
        with self.assertRaises(ValueError): select_decoder(dict(codecs=[candidate]), "missing")

    def test_missing_duplicate_reordered_and_wrong_timestamp_frames_fail(self):
        result = dict(status="pass", hardware=True, output_eos=True, sent_frames=2, decoded_frames=2, max_pending_frames=2,
                      frames=[dict(frame=i, pts_us=i*11111, queued_ns=100, output_ns=110, queue_to_output_ms=.00001) for i in range(2)])
        validate_frames(result, 2, [0, 11111])
        for key, value in (("decoded_frames", 1), ("hardware", False), ("output_eos", False), ("max_pending_frames", 5)):
            with self.assertRaises(ValueError): validate_frames(dict(result, **{key: value}), 2, [0, 11111])
        for key, value in (("frame", 0), ("pts_us", 0), ("output_ns", 99), ("queue_to_output_ms", float("nan"))):
            bad = copy.deepcopy(result); bad["frames"][1][key] = value
            with self.assertRaises(ValueError): validate_frames(bad, 2, [0, 11111])

    def test_quality_catches_panel_swap_and_range_compression(self):
        source = np.arange(3*16*16, dtype=np.uint8).reshape(16, 16, 3)
        self.assertTrue(rgb_metrics(source, source)["exact"])
        self.assertIsNone(rgb_metrics(source, source)["psnr_db"])
        self.assertFalse(rgb_metrics(source[:, :, ::-1], source)["exact"])
        limited = (source.astype(float)*219/255+16).astype(np.uint8)
        self.assertGreater(rgb_metrics(limited, source)["mse"], 0)
        with self.assertRaises(ValueError): rgb_metrics(source[:0], source[:0])

    def test_small_samples_cannot_claim_reliable_p99(self):
        result = distribution([3, 1, 2])
        self.assertEqual(result["p50"], 2)
        self.assertEqual(result["p95"], 3)
        self.assertTrue(result["p99_low_confidence"])
        self.assertFalse(distribution([])["measured"])

    def test_optional_unsupported_never_hides_failures_or_interruption(self):
        with tempfile.TemporaryDirectory() as folder:
            run = HardwareRun.__new__(HardwareRun)
            run.out = Path(folder); run.details = {}; run.tests = [{"status": "unsupported"}]
            run.completed = False
            self.assertEqual(run.summary()["status"], "incomplete")
            run.completed = True
            self.assertEqual(run.summary()["status"], "pass_with_limitations")
            run.tests.append({"status": "fail"})
            self.assertEqual(run.summary()["status"], "fail")


if __name__ == "__main__": unittest.main()
