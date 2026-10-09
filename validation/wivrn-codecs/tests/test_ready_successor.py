import copy
import importlib.util
from pathlib import Path
import unittest

from test_selection_counterfactual import fixture, sources

path = Path(__file__).resolve().parents[3] / 'prototypes/vr444/experiments/selection_policy/ready_successor.py'
spec = importlib.util.spec_from_file_location('ready_successor_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def triple():
    traces, presented = fixture()
    row = traces[0]
    for eye in range(2):
        row['history'][eye*3+2] = dict(row['history'][eye*3+1], slot=2, frame=3, warp=13, view_display_time_xr_ns=140)
    row['eligible_left_slots'] = [True]*3
    return traces, presented


class ReadySuccessorTests(unittest.TestCase):
    def test_advancement_requires_complete_strictly_later_successor(self):
        traces, _ = fixture()
        self.assertEqual(module.choose(traces[0])['selected'][0]['frame'], 1)
        traces, _ = triple()
        result = module.choose(traces[0])
        self.assertEqual(result['selected'][0]['frame'], 2)
        self.assertEqual(result['successor_left_slots'], [-1, 2, -1])
        self.assertEqual(result['supporting_left_slots'], [2])

    def test_second_horizon_endpoint_alpha_and_eye_metadata_guard_witness(self):
        for mutation in ('beyond', 'same_target', 'warp', 'missing_eye', 'alpha'):
            row = triple()[0][0]
            if mutation == 'beyond':
                row['history'][2]['view_display_time_xr_ns'] = row['history'][5]['view_display_time_xr_ns'] = 141
            elif mutation == 'same_target':
                row['history'][2]['view_display_time_xr_ns'] = row['history'][5]['view_display_time_xr_ns'] = 120
            elif mutation == 'warp':
                row['history'][5]['warp'] += 1
            elif mutation == 'missing_eye':
                row['history'][5] = dict(decoder=1, slot=2, present=False)
            else:
                row['history'][2]['alpha'] = row['history'][5]['alpha'] = True
            with self.subTest(mutation=mutation):
                self.assertEqual(module.choose(row)['selected'][0]['frame'], 1)

    def test_a_supporting_frame_requires_host_source_provenance_even_if_not_selected(self):
        traces, presented = triple()
        with self.assertRaisesRegex(ValueError, 'source-frame joins'):
            module.cf.evaluate(traces, presented, sources(), fractions=((1, 1),), warmup_ns=0,
                               selector=module.choose, selector_name='ready_successor')
        result = module.cf.evaluate(traces, presented, sources(frames=(1, 2, 3)), fractions=((1, 1),), warmup_ns=0,
                                    selector=module.choose, selector_name='ready_successor')
        self.assertEqual(result['source_validation']['analyzed_frames'], 3)
        self.assertFalse(result['measured_speedup'])

    def test_forward_guard_can_retain_old_pair_without_inventing_a_reservation(self):
        row = triple()[0][0]
        previous = copy.deepcopy(row['selected'])
        for f in previous[:2]:
            f.update(frame=4, warp=14, view_display_time_xr_ns=150)
        result = module.choose(row, previous=previous)
        self.assertTrue(result['fallback'])
        self.assertEqual(result['selected'], previous)
        self.assertEqual(result['supporting_left_slots'], [])

