import copy
import importlib.util
from pathlib import Path
import unittest

from test_selection_counterfactual import fixture

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('cardinality_audit_test', ROOT / 'prototypes/vr444/experiments/selection_policy/cardinality.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def audit_fixture():
    frames = [dict(frame=1, target=100, receipt=1, ready=2), dict(frame=2, target=110, receipt=12, ready=13)]
    deadlines = [dict(sequence=1, selection=10, submit=11), dict(sequence=2, selection=20, submit=21)]
    output = dict(schema=1, frontier=[dict(skips=0, required_count=2, feasible=True, maximum_good=2,
        certificate=[[1, 1], [2, 2]]), dict(skips=1, required_count=1, feasible=True, maximum_good=1, certificate=[[2, 2]])])
    return frames, deadlines, output


class CardinalityAuditTests(unittest.TestCase):
    def test_certificate_age_count_cadence_and_hash_recomputed(self):
        frames, deadlines, output = audit_fixture()
        result = module.audit_frontier(output, frames, deadlines, 1, 15, {1: 100, 2: 110})
        self.assertEqual(result['frontier'][0]['required_good_count_for_p95'], 2)
        self.assertEqual(result['frontier'][0]['conditional_unique_frame_cadence_hz'], 100_000_000)
        self.assertTrue(result['frontier'][0]['p95_under_threshold_attainable_in_model'])
        self.assertEqual(len(result['frontier'][0]['certificate_sha256']), 64)
        self.assertIsNone(result['frontier'][1]['conditional_unique_frame_cadence_hz'])

    def test_dishonest_or_malformed_solver_certificates_fail(self):
        for mutation in ('schema', 'missing', 'score', 'bool_score', 'count', 'repeat_deadline', 'repeat_frame', 'invented', 'not_ready', 'infeasible_path'):
            frames, deadlines, output = audit_fixture()
            entry = output['frontier'][0]
            if mutation == 'schema': output['schema'] = True
            elif mutation == 'missing': output['frontier'].pop()
            elif mutation == 'score': entry['maximum_good'] = 1
            elif mutation == 'bool_score': entry['maximum_good'] = True
            elif mutation == 'count': entry['required_count'] = 1
            elif mutation == 'repeat_deadline': entry['certificate'][1][0] = 1
            elif mutation == 'repeat_frame': entry['certificate'][1][1] = 1
            elif mutation == 'invented': entry['certificate'][1][1] = 3
            elif mutation == 'not_ready': frames[1]['ready'] = 30
            else: entry['feasible'] = False
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.audit_frontier(output, frames, deadlines, 1, 15, {1: 100, 2: 110})

    def test_observed_warm_prefix_excludes_prior_firsts_and_preserves_forward_identity(self):
        traces, presented = fixture()
        known = module.supply.known_pairs(traces)
        cutoff = presented[0]['submit_xr_ns'] + 1
        relaxed, _ = module.warm_population(known, traces, presented, cutoff, 'relaxed_known_universe')
        fixed, contract = module.warm_population(known, traces, presented, cutoff, 'observed_submission_prefix')
        self.assertEqual([f['frame'] for f in relaxed], [1, 2])
        self.assertEqual([f['frame'] for f in fixed], [2])
        self.assertEqual(contract['last_actual_pre_warm_frame'], 1)
        self.assertTrue(contract['pre_warm_first_identity_preserved'])
        fixed, contract = module.warm_population(known, traces, presented, 0, 'observed_submission_prefix')
        self.assertEqual([f['frame'] for f in fixed], [1, 2])
        self.assertIsNone(contract['last_actual_pre_warm_frame'])

    def test_nonmonotonic_source_targets_fail_solver_population_contract(self):
        traces, presented = fixture()
        known = module.supply.known_pairs(traces)
        known[2]['target'] = 99
        with self.assertRaises(ValueError):
            module.warm_population(known, traces, presented, 0, 'relaxed_known_universe')
        with self.assertRaises(ValueError):
            module.warm_population(known, traces, presented, 0, 'invented_model')


if __name__ == '__main__':
    unittest.main()
