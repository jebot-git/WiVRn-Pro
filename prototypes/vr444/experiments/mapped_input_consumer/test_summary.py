import copy
import importlib.util
import unittest
from pathlib import Path
from summarize import comparisons, summarize

_spec = importlib.util.spec_from_file_location('readiness_summary_fixture', Path(__file__).resolve().parents[1] / 'mapped_input_decode/test_summary.py')
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)


def report():
    result = fixture_module.report()
    result.update(experiment='mapped-input-consumer', first_consumer_audited=True,
                  right_eye_omission_controls=234)
    for row in result['per_case']:
        for arm in row['arms'].values():
            median = arm['outer_wall']['p50_ms'] + 1
            arm['joint_outer_wall'] = dict(samples=31, p50_ms=median, p95_ms=median+2, max_ms=median+3)
        row['arms']['rrf_direct_default']['joint_outer_wall']['p50_ms'] += .5
    return result


class ConsumerSummaryTests(unittest.TestCase):
    def test_joint_and_readiness_quantiles_remain_distinct(self):
        result = summarize([report()])
        ready = result['captured_readiness_paired_comparisons'][0]['comparisons'][0]
        joint = result['captured_joint_paired_comparisons'][0]['comparisons'][0]
        self.assertEqual(ready['paired_differences']['p50_ms']['median_candidate_minus_baseline_ms'], 1)
        self.assertEqual(joint['paired_differences']['p50_ms']['median_candidate_minus_baseline_ms'], 1.5)

    def test_consumer_evidence_cannot_be_missing(self):
        for key, value in [('experiment', 'mapped-input-decode'), ('first_consumer_audited', False),
                           ('right_eye_omission_controls', 233), ('right_eye_omission_controls', 234.)]:
            changed = report(); changed[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): summarize([changed])

    def test_joint_statistics_must_be_complete(self):
        for key, value in [('samples', 30), ('samples', True), ('p50_ms', float('nan')),
                           ('p95_ms', -1), ('max_ms', 0), ('p50_ms', True)]:
            changed = report(); changed['per_case'][0]['arms']['rrf_cpu_reference']['joint_outer_wall'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): summarize([changed])
        changed = report(); changed['per_case'].pop()
        with self.assertRaises(ValueError): summarize([changed])


if __name__ == '__main__':
    unittest.main()
