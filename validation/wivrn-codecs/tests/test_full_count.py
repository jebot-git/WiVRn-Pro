import copy
import importlib.util
from itertools import combinations
from pathlib import Path
import random
import unittest

from test_selection_counterfactual import fixture, sources

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('full_count_test', ROOT / 'prototypes/vr444/experiments/selection_policy/full_count.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def brute(frames, deadlines, threshold):
    best = None
    for path in combinations(sorted(frames, key=lambda f: f['frame']), len(deadlines)):
        if any(a['target'] > b['target'] for a, b in zip(path, path[1:])):
            continue
        if any(f['ready'] > d['selection'] for f, d in zip(path, deadlines)):
            continue
        good = sum(d['submit'] - f['receipt'] < threshold for f, d in zip(path, deadlines))
        best = good if best is None else max(best, good)
    return best


class FullCountTests(unittest.TestCase):
    def test_512_random_small_populations_match_exhaustive_combinations(self):
        rng = random.Random(94171)
        for case in range(512):
            frames = []
            for i in range(rng.randrange(1, 9)):
                receipt = rng.randrange(1, 51)
                frames.append(dict(frame=i + 1, target=rng.randrange(1, 16), receipt=receipt, ready=receipt + rng.randrange(8)))
            deadlines = [dict(sequence=i * 2 + 1, selection=12 + i * 10, submit=15 + i * 10) for i in range(rng.randrange(1, 6))]
            expected = brute(frames, deadlines, 15)
            actual = module.solve(frames, deadlines, 15)
            self.assertEqual(actual['full_count_feasible'], expected is not None, case)
            self.assertEqual(actual['maximum_under_threshold_count'], expected, case)
            if expected is not None:
                self.assertEqual(len(actual['certificate']), len(deadlines))
                self.assertEqual(actual['required_good_count_for_p95'], (95 * len(deadlines) + 99) // 100)

    def test_necessary_good_count_does_not_prove_joint_full_count(self):
        frames = [dict(frame=i + 1, target=100 + i, receipt=i * 10 + 1, ready=i * 10 + 1) for i in range(20)]
        deadlines = [dict(sequence=i + 1, selection=i * 10 + 19, submit=i * 10 + 20) for i in range(20)]
        # Skipping the last deadline allows nineteen good choices (frames 2..20),
        # satisfying the loose ceil(.95*20) count condition. Requiring twenty
        # distinct choices forces frames 1..20 and every one has age19 >=15.
        nodes = [dict(sequence=d['sequence'], frame=f['frame'], target=f['target'],
            age_ns=d['submit'] - f['receipt'], threshold_ns=15)
            for d in deadlines for f in frames if f['ready'] <= d['selection'] and d['submit'] - f['receipt'] < 15]
        self.assertEqual(module.oracle.optimal_chain(nodes, 'under_threshold_first')['under_threshold_count'], 19)
        result = module.solve(frames, deadlines, 15)
        self.assertTrue(result['full_count_feasible'])
        self.assertEqual(result['maximum_under_threshold_count'], 0)
        self.assertFalse(result['p95_under_threshold_attainable_in_model'])
        self.assertEqual(result['maximum_rank_band_width'], 1)

    def test_target_order_readiness_and_exact_threshold(self):
        frames = [dict(frame=1, target=30, receipt=5, ready=5),
                  dict(frame=2, target=10, receipt=5, ready=5),
                  dict(frame=3, target=20, receipt=15, ready=15)]
        deadlines = [dict(sequence=1, selection=10, submit=15), dict(sequence=2, selection=20, submit=25)]
        result = module.solve(frames, deadlines, 10)
        self.assertEqual(result['certificate'], [[1, 2], [2, 3]])
        self.assertEqual(result['maximum_under_threshold_count'], 0)  # age exactly10 is not good
        self.assertEqual(module.solve(list(reversed(frames)), deadlines, 10), result)
        for f in frames:
            f['ready'] = 100
        self.assertFalse(module.solve(frames, deadlines, 10)['full_count_feasible'])

    def test_sealed_population_join_and_clock_translation(self):
        traces, presented = fixture()
        for slot in (1, 4):
            traces[0]['history'][slot].update(receive_xr_ns=40, ready_xr_ns=45)
        result = module.evaluate(traces, presented, sources(), scenarios=((0, 0),), warmup_ns=0, threshold_ns=30)
        self.assertEqual(result['alternatives'][0]['maximum_under_threshold_count'], 1)
        shifted = module.evaluate(traces, presented, sources(clock_offset=10**14), scenarios=((0, 0),), warmup_ns=0, threshold_ns=30)
        self.assertEqual(result['alternatives'], shifted['alternatives'])
        for key in ('measured_speedup', 'latency_gate_evaluated', 'runnable_policy', 'native_behavior_changed', 'promotion_eligible', 'release_ready'):
            self.assertIs(result[key], False)
        with self.assertRaises(ValueError):
            module.evaluate(traces, presented, sources(frames=(1,)), scenarios=((0, 0),), warmup_ns=0)

    def test_bad_solver_contract_and_bounded_state_work(self):
        frame = dict(frame=1, target=1, receipt=1, ready=1)
        deadline = dict(sequence=1, selection=2, submit=3)
        self.assertFalse(module.solve([], [deadline])['full_count_feasible'])
        for key in frame:
            with self.subTest(key=key), self.assertRaises(ValueError):
                module.solve([frame | {key: True}], [deadline])
        for frames, deadlines, threshold in (([frame, frame], [deadline], 15),
                ([frame | dict(receipt=2, ready=1)], [deadline], 15), ([frame], [], 15),
                ([frame], [deadline, deadline], 15), ([frame], [deadline | dict(selection=4)], 15),
                ([frame], [deadline], True), ([frame], [deadline], 0)):
            with self.assertRaises(ValueError):
                module.solve(frames, deadlines, threshold)
        frames = [frame | dict(frame=i) for i in range(6000)]
        deadlines = [dict(sequence=i + 1, selection=i + 1, submit=i + 2) for i in range(3000)]
        with self.assertRaisesRegex(ValueError, 'state budget'):
            module.solve(frames, deadlines)

    def test_scenarios_are_explicit_bounded_and_no_earlier_admission(self):
        traces, presented = fixture()
        for scenarios in ((), ((0, 0), (0, 0)), ((-1, 0),), ((0, -1),), ((True, 0),), ((0, 50_000_001),), ((0,),)):
            with self.subTest(scenarios=scenarios), self.assertRaises(ValueError):
                module.evaluate(traces, presented, sources(), scenarios=scenarios, warmup_ns=0)
        corrupted = copy.deepcopy(traces)
        corrupted[0]['history'][1]['ready_xr_ns'] = 1000
        with self.assertRaises(ValueError):
            module.evaluate(corrupted, presented, sources(), scenarios=((0, 0),), warmup_ns=0)


if __name__ == '__main__':
    unittest.main()
