import importlib.util
from pathlib import Path
import unittest
from test_latency_policy import rows

spec = importlib.util.spec_from_file_location('research_latency_test', Path(__file__).resolve().parents[1]/'integration/research_latency.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class ResearchLatency(unittest.TestCase):
    def test_same_strict_boundary_for_both_independent_candidates(self):
        for mode in ('custom-vr444', 'custom-rrb32'):
            provenance = module.policy.load_policy()['profile'] | dict(mode=mode)
            for latency, status in ((14.999999, 'pass'), (15, 'fail'), (15.000001, 'fail')):
                result = module.evaluate(rows(latency), provenance)
                self.assertEqual(result['status'], status)
                self.assertFalse(result['goal_verified'])
                self.assertFalse(result['promotion_eligible'])
            self.assertEqual(module.evaluate(rows(12, hz=70), provenance)['status'], 'fail')

    def test_incomplete_profile_or_duration_cannot_pass(self):
        provenance = module.policy.load_policy()['profile'] | dict(mode='custom-rrb32')
        self.assertEqual(module.evaluate(rows(12)[:200], provenance)['status'], 'fail')
        self.assertEqual(module.evaluate(rows(12), provenance | dict(transport='UDP'))['status'], 'fail')
        self.assertEqual(module.evaluate(rows(12), provenance | dict(mode='native-hevc'))['status'], 'fail')
        self.assertEqual(module.evaluate(rows(12), provenance | dict(quality_application=True))['status'], 'fail')
