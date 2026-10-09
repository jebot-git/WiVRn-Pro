import copy
import importlib.util
from pathlib import Path
import unittest

from test_selection_counterfactual import fixture, sources

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('target_envelope_test', ROOT / 'prototypes/vr444/experiments/selection_policy/target_envelope.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


class TargetEnvelopeTests(unittest.TestCase):
    def test_existing_envelopes_reproduce_original_oracle(self):
        traces, presented = fixture()
        old = module.oracle.evaluate(traces, presented, sources(), warmup_ns=0)
        new = module.evaluate(traces, presented, sources(), envelopes=((0, 1), (1, 2), (1, 1)), warmup_ns=0)
        for before, after in zip(old['alternatives'], new['alternatives']):
            self.assertEqual(before['available_nodes'], after['available_nodes'])
            for a, b in zip(before['oracle_paths'], after['oracle_paths']):
                for key in ('unique_count', 'under_threshold_count', 'unique_latched_app_sets', 'certificate'):
                    self.assertEqual(a[key], b[key])
            for key in ('unique_count', 'percentile', 'minimum_age_ns', 'certificate'):
                self.assertEqual(before['best_p95_at_maximum_unique_count'][key], after['best_p95_at_maximum_unique_count'][key])
        for key in ('measured_speedup', 'latency_gate_evaluated', 'runnable_policy', 'native_behavior_changed', 'promotion_eligible', 'release_ready'):
            self.assertIs(new[key], False)

    def test_larger_envelope_inclusive_boundary_without_invented_pairs(self):
        for target, expected in ((140, [1, 2, 2]), (141, [1, 1, 2])):
            traces, presented = fixture()
            for slot in (1, 4):
                traces[0]['history'][slot]['view_display_time_xr_ns'] = target
            result = module.evaluate(traces, presented, sources(), envelopes=((1, 1), (2, 1), None), warmup_ns=0)
            self.assertEqual([a['available_nodes'] for a in result['alternatives']], expected)
            for alternative in result['alternatives']:
                for path in alternative['oracle_paths']:
                    self.assertTrue(all(sequence == 1 and frame in (1, 2) for sequence, frame in path['certificate']))
            self.assertFalse(result['alternatives'][1]['within_existing_one_period_limit'])
            self.assertTrue(result['alternatives'][2]['target_ceiling_removed'])

    def test_zero_runtime_interval_does_not_create_finite_future_allowance(self):
        traces, presented = fixture()
        traces[0]['previous_predicted_display_xr_ns'] = 0
        presented[0]['previous_predicted_display_xr_ns'] = 0
        traces[0]['max_ahead_xr_ns'] = presented[0]['max_ahead_xr_ns'] = 0
        result = module.evaluate(traces, presented, sources(), envelopes=((4, 1), None), warmup_ns=0)
        self.assertEqual([a['available_nodes'] for a in result['alternatives']], [1, 2])

    def test_ratio_reduction_integer_rounding_and_large_timestamps(self):
        self.assertEqual(module.validate_envelopes(((2, 4), (0, 8), None)), ((1, 2), (0, 1), None))
        n = dict(runtime_target=2**63-100, runtime_interval_bound_ns=11, target=2**63-84)
        self.assertTrue(module.within(n, (3, 2)))  # floor(11*3/2) == 16
        self.assertFalse(module.within(n | {'target': n['target'] + 1}, (3, 2)))
        self.assertTrue(module.within(n | {'target': 2**63-1}, None))

    def test_missing_join_unavailable_frame_and_corrupt_contract_fail(self):
        traces, presented = fixture()
        # Even an envelope excluding frame 2 requires its observed population
        # to be joined, so results cannot silently drop unsupported inputs.
        with self.assertRaises(ValueError):
            module.evaluate(traces, presented, sources(frames=(1,)), envelopes=((0, 1),), warmup_ns=0)
        for what in ('not_ready', 'warp', 'source_drop', 'source_domain'):
            t, p, s = copy.deepcopy(traces), copy.deepcopy(presented), sources()
            if what == 'not_ready':
                t[0]['history'][1]['ready_xr_ns'] = t[0]['selection_end_xr_ns'] + 1
            elif what == 'warp':
                t[0]['selected'][0]['warp'] += 1
            elif what == 'source_drop':
                s[-1]['dropped'] = 1
            else:
                s[0]['clock'] = 'client'
            with self.subTest(what=what), self.assertRaises(ValueError):
                module.evaluate(t, p, s, warmup_ns=0)

    def test_host_clock_translation_does_not_change_client_bounds(self):
        traces, presented = fixture()
        a = module.evaluate(traces, presented, sources(), warmup_ns=0)
        b = module.evaluate(traces, presented, sources(clock_offset=10**14), warmup_ns=0)
        self.assertEqual(a['alternatives'], b['alternatives'])

    def test_invalid_envelopes_warmup_and_threshold_fail(self):
        for envelopes in ((), (None, None), ((1, 1), (2, 2)), ((0, 1), (0, 3)),
                          ((5, 1),), ((1, 0),), ((1, 1025),), ((True, 1),), ((1.0, 1),), ((1,),), ('unbounded',)):
            with self.subTest(envelopes=envelopes), self.assertRaises(ValueError):
                module.validate_envelopes(envelopes)
        traces, presented = fixture()
        for options in (dict(warmup_ns=-1), dict(warmup_ns=True), dict(threshold_ns=0), dict(threshold_ns=True), dict(warmup_ns=1000)):
            with self.assertRaises(ValueError):
                module.evaluate(traces, presented, sources(), **options)


if __name__ == '__main__':
    unittest.main()
