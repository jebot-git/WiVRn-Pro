import copy
import importlib.util
from pathlib import Path
import random
import unittest

from test_selection_counterfactual import fixture, sources

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('availability_oracle_test', ROOT / 'prototypes/vr444/experiments/selection_policy/availability_oracle.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def node(sequence, frame, target=100, age=1, threshold=15):
    return dict(sequence=sequence, frame=frame, target=target, age_ns=age, threshold_ns=threshold)


def brute(nodes, objective):
    """Exhaust all subsets, independently of Fenwick prefix structures."""
    best = (0, 0)
    for mask in range(1 << len(nodes)):
        chosen = [n for i, n in enumerate(nodes) if mask & (1 << i)]
        if any(a['sequence'] >= b['sequence'] or a['frame'] >= b['frame'] or a['target'] > b['target']
               for a, b in zip(chosen, chosen[1:])):
            continue
        good = sum(n['age_ns'] < n['threshold_ns'] for n in chosen)
        score = (len(chosen), good) if objective == 'unique_first' else (good, len(chosen))
        best = max(best, score)
    return best


class AvailabilityOracleTests(unittest.TestCase):
    def test_one_per_deadline_unique_frame_and_nondecreasing_target(self):
        candidates = [node(1, 1), node(1, 2), node(2, 1), node(2, 2), node(3, 3, 99), node(4, 4)]
        result = module.optimal_chain(candidates, 'unique_first')
        self.assertEqual(result['unique_count'], 3)
        self.assertEqual(result['under_threshold_count'], 3)
        path = [candidates[i] for i in result['path']]
        self.assertEqual([n['frame'] for n in path], [1, 2, 4])

    def test_strict_age_boundary_and_distinct_objectives(self):
        candidates = [node(1, 1, 300), node(2, 2, 300, 15), node(3, 3, 300, 15),
                      node(4, 4, 100), node(5, 5, 100)]
        unique = module.optimal_chain(candidates, 'unique_first')
        good = module.optimal_chain(candidates, 'under_threshold_first')
        self.assertEqual((unique['unique_count'], unique['under_threshold_count']), (3, 1))
        self.assertEqual((good['unique_count'], good['under_threshold_count']), (2, 2))

    def test_randomized_prefix_solver_matches_exhaustive_subsets(self):
        rng = random.Random(20261009)
        for case in range(256):
            candidates = [node(i // 3 + 1, rng.randrange(1, 9), rng.randrange(1, 9), rng.randrange(1, 21))
                          for i in range(rng.randrange(1, 12))]
            for objective in ('unique_first', 'under_threshold_first'):
                result = module.optimal_chain(candidates, objective)
                self.assertEqual(tuple(result['score']), brute(candidates, objective), (case, objective, candidates))

    def test_quantile_optimum_at_max_count_matches_exhaustive_subsets(self):
        rng = random.Random(1754)
        for case in range(64):
            candidates = [node(i // 2 + 1, rng.randrange(1, 9), rng.randrange(1, 9), rng.randrange(1, 21))
                          for i in range(rng.randrange(1, 11))]
            for percentile in (50, 95, 100):
                all_paths = []
                for mask in range(1, 1 << len(candidates)):
                    path = [n for i, n in enumerate(candidates) if mask & (1 << i)]
                    if any(a['sequence'] >= b['sequence'] or a['frame'] >= b['frame'] or a['target'] > b['target']
                           for a, b in zip(path, path[1:])):
                        continue
                    ages = sorted(n['age_ns'] for n in path)
                    all_paths.append((len(path), ages[(percentile * len(path) + 99) // 100 - 1]))
                count = max(n for n, age in all_paths)
                best_age = min(age for n, age in all_paths if n == count)
                result = module.best_quantile_at_max_count(candidates, percentile)
                self.assertEqual((result['unique_count'], result['minimum_age_ns']), (count, best_age), case)
        self.assertIsNone(module.best_quantile_at_max_count([])['minimum_age_ns'])
        for value in (0, 101, True, 1.0):
            with self.assertRaises(ValueError):
                module.best_quantile_at_max_count([], value)

    def test_solver_empty_unknown_unsorted_and_invalid_node_contract(self):
        self.assertEqual(module.optimal_chain([], 'unique_first')['score'], [0, 0])
        with self.assertRaises(ValueError):
            module.optimal_chain([], 'imaginary')
        with self.assertRaises(ValueError):
            module.optimal_chain([node(2, 1), node(1, 2)], 'unique_first')
        for field in node(1, 1):
            broken = node(1, 1); broken[field] = True
            with self.assertRaises(ValueError):
                module.optimal_chain([broken], 'unique_first')
        for field in ('sequence', 'frame', 'target', 'age_ns', 'threshold_ns'):
            broken = node(1, 1); broken[field] = -1
            with self.assertRaises(ValueError):
                module.optimal_chain([broken], 'unique_first')

    def test_snapshot_horizon_and_join_contract(self):
        traces, presented = fixture()
        result = module.evaluate(traces, presented, sources(), warmup_ns=0)
        self.assertEqual(result['warm_observed_deadlines'], 1)
        self.assertEqual(result['alternatives'][0]['available_nodes'], 1)
        self.assertEqual(result['alternatives'][-1]['available_nodes'], 2)
        self.assertEqual(result['alternatives'][-1]['oracle_paths'][0]['unique_count'], 1)
        for key in ('measured_speedup', 'latency_gate_evaluated', 'promotion_eligible', 'release_ready'):
            self.assertIs(result[key], False)
        with self.assertRaises(ValueError):
            module.evaluate(traces, presented, sources(frames=(1,)), warmup_ns=0)
        shifted = module.evaluate(traces, presented, sources(clock_offset=10**14), warmup_ns=0)
        self.assertEqual(result['alternatives'], shifted['alternatives'])

    def test_trace_corruption_and_bad_parameter_fail_closed(self):
        traces, presented = fixture()
        for options in (dict(warmup_ns=-1), dict(warmup_ns=True), dict(threshold_ns=0),
                        dict(fractions=()), dict(fractions=((1, 1), (1, 1))),
                        dict(fractions=((True, 1),)), dict(fractions=((2, 1),))):
            with self.assertRaises(ValueError):
                module.evaluate(traces, presented, sources(), **options)
        corrupt = copy.deepcopy(traces)
        corrupt[0]['history'][0]['ready_xr_ns'] = corrupt[0]['selection_end_xr_ns'] + 1
        with self.assertRaises(ValueError):
            module.evaluate(corrupt, presented, sources(), warmup_ns=0)


if __name__ == '__main__':
    unittest.main()
