import copy
import importlib.util
from pathlib import Path
import unittest

from test_selection_counterfactual import fixture, sources
from test_candidate_trace import fallback_after

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('supply_phase_test', ROOT / 'prototypes/vr444/experiments/selection_policy/supply_phase.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fresh_second_pair():
    traces, presented = fixture()
    for slot in (1, 4):
        traces[0]['history'][slot].update(receive_xr_ns=40, ready_xr_ns=45)
    return traces, presented


class SupplyPhaseTests(unittest.TestCase):
    def evaluate(self, traces, presented, **kwargs):
        return module.evaluate(traces, presented, sources(), warmup_ns=0, shifts_ns=kwargs.pop('shifts_ns', (0,)),
            publication_advances_ns=kwargs.pop('publication_advances_ns', (0,)), **kwargs)

    def test_exact_receipt_age_and_selection_readiness_boundaries(self):
        traces, presented = fresh_second_pair()
        result = self.evaluate(traces, presented, shifts_ns=(-10, 0, 5), threshold_ns=30)
        self.assertEqual([a['maximum_advancing_under_threshold_count'] for a in result['alternatives']], [0, 1, 0])
        self.assertEqual(result['observed_snapshot_no_target_ceiling']['maximum_advancing_under_threshold_count'], 1)
        self.assertEqual(result['alternatives'][1]['certificate'], [[1, 2]])
        # At -10ns the pair is not ready. At +5ns its age is exactly 30ns,
        # which must not enter a strictly-under-30ns population.
        for key in ('measured_speedup', 'latency_gate_evaluated', 'runnable_policy', 'native_behavior_changed', 'promotion_eligible', 'release_ready'):
            self.assertIs(result[key], False)

    def test_publication_advance_is_clipped_at_each_eye_receipt(self):
        traces, presented = fresh_second_pair()
        result = self.evaluate(traces, presented, shifts_ns=(-10,), publication_advances_ns=(0, 5, 20), threshold_ns=30)
        self.assertEqual([a['maximum_advancing_under_threshold_count'] for a in result['alternatives']], [0, 1, 1])
        self.assertEqual([a['with_recorded_admission_floor']['maximum_advancing_under_threshold_count']
                          for a in result['alternatives']], [0, 0, 0])
        self.assertEqual(result['alternatives'][1]['chosen_before_recorded_admission'], 1)
        traces[0]['history'][4].update(receive_xr_ns=49, ready_xr_ns=49)
        result = self.evaluate(traces, presented, shifts_ns=(-10,), publication_advances_ns=(0, 20), threshold_ns=30)
        self.assertEqual([a['maximum_advancing_under_threshold_count'] for a in result['alternatives']], [0, 0])

    def test_later_observed_pair_use_is_explicit_not_silently_original_availability(self):
        traces, presented = fresh_second_pair()
        initial = copy.deepcopy(traces[0])
        r, p = traces[0], presented[0]
        for slot in (1, 4):
            r['history'][slot] = dict(decoder=slot // 3, slot=slot % 3, present=False)
        r['eligible_left_slots'] = [True, False, False]
        r2, p2 = fallback_after(r, p)
        r2.update(history=initial['history'], reason='closest_target', repeat_previous=False,
            eligible_left_slots=[True, True, False], chosen_slots=[1, 1, -1])
        selected = [copy.deepcopy(r2['history'][1]), copy.deepcopy(r2['history'][4]), dict(decoder=2, present=False)]
        for f in selected:
            f.pop('slot', None)
        r2['closest'] = copy.deepcopy(selected)
        r2['selected'] = selected
        p2.update(left=2, right=2, left_warp=12, right_warp=12,
            left_receive_xr_ns=40, right_receive_xr_ns=40, left_decode_ready_xr_ns=45, right_decode_ready_xr_ns=45)
        result = self.evaluate([r, r2], [p, p2], threshold_ns=40)
        self.assertEqual(result['observed_snapshot_no_target_ceiling']['maximum_advancing_under_threshold_count'], 0)
        alternative = result['alternatives'][0]
        self.assertEqual(alternative['certificate'], [[1, 2]])
        self.assertEqual(alternative['chosen_absent_from_original_snapshot'], 1)
        self.assertEqual(alternative['chosen_before_first_recorded_observation'], 1)

    def test_all_participating_eyes_must_be_ready(self):
        traces, presented = fresh_second_pair()
        known = module.known_pairs(traces)
        pair = known[2]['pair']
        for f in pair[:2]:
            f['alpha'] = True
        pair[2] = pair[0] | dict(decoder=2, receive_xr_ns=45, ready_xr_ns=55)
        nodes = module.available_nodes(traces, presented, [dict(sequence=1)], known, 0, 0, 30)
        self.assertEqual(nodes, [])
        nodes = module.available_nodes(traces, presented, [dict(sequence=1)], known, 0, 5, 30)
        self.assertEqual([n['frame'] for n in nodes], [2])

    def test_clock_translation_and_source_failure_boundaries(self):
        traces, presented = fresh_second_pair()
        a = self.evaluate(traces, presented, threshold_ns=30)
        b = module.evaluate(traces, presented, sources(clock_offset=10**14), warmup_ns=0,
            shifts_ns=(0,), publication_advances_ns=(0,), threshold_ns=30)
        self.assertEqual(a['alternatives'], b['alternatives'])
        with self.assertRaises(ValueError):
            module.evaluate(traces, presented, sources(frames=(1,)), warmup_ns=0, shifts_ns=(0,), publication_advances_ns=(0,))

    def test_recorded_admission_floor_distinguishes_early_and_after_fence(self):
        traces, presented = fixture()
        r, p = traces[0], presented[0]
        a = module.observed_admission(r, p, 45, 60)
        self.assertFalse(a['before_recorded_admission'])
        r = r | dict(selection_phase='after-fence')
        a = module.observed_admission(r, p, 45, 60)
        self.assertTrue(a['before_required_recorded_fence'])
        self.assertTrue(a['before_recorded_admission'])
        a = module.observed_admission(r, p, p['render_fence_end_xr_ns'], 100)
        self.assertFalse(a['before_recorded_admission'])
        self.assertTrue(a['submit_at_or_after_original_runtime_prediction'])

    def test_bad_parameters_and_invalid_shifted_local_timestamps_fail(self):
        traces, presented = fresh_second_pair()
        for options in (dict(shifts_ns=()), dict(shifts_ns=(0, 0)), dict(shifts_ns=(True,)),
                        dict(shifts_ns=(50_000_001,)), dict(shifts_ns=(-51,)),
                        dict(publication_advances_ns=()), dict(publication_advances_ns=(0, 0)),
                        dict(publication_advances_ns=(-1,)), dict(publication_advances_ns=(True,)),
                        dict(publication_advances_ns=(50_000_001,)), dict(threshold_ns=0)):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.evaluate(traces, presented, **options)


if __name__ == '__main__':
    unittest.main()
