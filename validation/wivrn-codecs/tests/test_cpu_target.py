import importlib.util
from pathlib import Path
import unittest
from test_latency_policy import rows

spec = importlib.util.spec_from_file_location('cpu_target', Path(__file__).resolve().parents[1]/'integration/cpu_target.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class CpuTargetTests(unittest.TestCase):
    def provenance(self):
        return module.policy.load_policy()['profile'] | dict(mode='custom-vr444', decoder_backend='cpu-neon')

    def test_strict_p95_boundary_and_no_promotion(self):
        for value, expected in ((14.999999, 'pass'), (15, 'fail'), (15.000001, 'fail')):
            result = module.evaluate(rows(value), self.provenance())
            self.assertEqual(result['status'], expected)
            self.assertFalse(result['promotion_eligible'])
            self.assertFalse(result['color_quality_verified'])

    def test_tail_and_cadence_still_matter(self):
        data = rows(12)
        for row in data[1000:]:
            row['left_receive_xr_ns'] -= 10_000_000
        self.assertEqual(module.evaluate(data, self.provenance())['status'], 'fail')
        self.assertEqual(module.evaluate(rows(12, hz=70), self.provenance())['status'], 'fail')

    def test_wrong_path_empty_and_missing_metadata_fail(self):
        for changed in ({'decoder_backend': 'resident'}, {'mode': 'native-hevc'}, {'transport': 'UDP'}):
            self.assertEqual(module.evaluate(rows(12), self.provenance() | changed)['status'], 'fail')
        self.assertEqual(module.evaluate([], self.provenance())['status'], 'fail')
        self.assertEqual(module.evaluate(rows(12), {})['status'], 'fail')
