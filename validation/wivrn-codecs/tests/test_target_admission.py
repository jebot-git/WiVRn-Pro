import copy
import importlib.util
from pathlib import Path
import random
import unittest

spec = importlib.util.spec_from_file_location('target_admission_test', Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/target_admission.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def population():
    frames = [dict(frame=i, target=t, receipt=1, ready=2) for i, t in enumerate((10, 20, 21, 40))]
    deadlines = [dict(sequence=i+1, selection=10*(i+1), submit=10*(i+1)+1) for i in range(3)]
    return frames, deadlines, [10, 20, 30]


class TargetAdmissionTests(unittest.TestCase):
    def test_inclusive_crossing_and_never_admissible(self):
        frames, deadlines, ceilings = population()
        transformed, receipt = module.eligibility_frames(frames, deadlines, ceilings)
        self.assertEqual([f['ready'] for f in transformed], [10, 20, 30])
        self.assertEqual(receipt['frames_never_target_admissible'], 1)
        self.assertFalse(receipt['synthetic_floor_is_publication_timestamp'])
        self.assertEqual(frames[0]['ready'], 2)
        self.assertEqual(module.eligibility_frames(frames, deadlines, None)[0], frames)

    def test_all_edges_equivalent_in_1024_random_populations(self):
        rng = random.Random(0x15CAFE)
        for _ in range(1024):
            n, d = rng.randrange(0, 10), rng.randrange(1, 10)
            targets = sorted(rng.randrange(1, 120) for _ in range(n))
            frames = [dict(frame=i, target=t, receipt=rng.randrange(1, 10), ready=rng.randrange(10, 150)) for i, t in enumerate(targets)]
            deadlines = [dict(sequence=i+1, selection=20*(i+1), submit=20*(i+1)+1) for i in range(d)]
            ceilings = sorted(rng.randrange(1, 120) for _ in range(d))
            transformed, _ = module.eligibility_frames(frames, deadlines, ceilings)
            converted = {f['frame']: f for f in transformed}
            for f in frames:
                for j, deadline in enumerate(deadlines):
                    original = f['ready'] <= deadline['selection'] and f['target'] <= ceilings[j]
                    actual = f['frame'] in converted and converted[f['frame']]['ready'] <= deadline['selection']
                    self.assertEqual(original, actual)

    def test_native_horizon_is_recomputed_even_when_closest_logged_zero(self):
        row = dict(predicted_display_xr_ns=100, predicted_display_period_xr_ns=12,
                   previous_predicted_display_xr_ns=89, max_ahead_xr_ns=0)
        d = [dict(sequence=1, selection=80, submit=82)]
        self.assertEqual(module.target_ceilings([row], d, 1), [111])
        self.assertEqual(module.target_ceilings([row], d, 2), [122])
        self.assertEqual(module.target_ceilings([row], d, 0), [100])
        row['previous_predicted_display_xr_ns'] = 0
        self.assertEqual(module.target_ceilings([row], d, 1), [100])

    def test_bad_ceiling_or_unordered_input_rejects(self):
        for mutation in range(14):
            f, d, c = population()
            if mutation == 0: c = [20, 10, 30]
            elif mutation == 1: c.pop()
            elif mutation == 2: c[0] = True
            elif mutation == 3: c[0] = 0
            elif mutation == 4: c[-1] = 2**63
            elif mutation == 5: d[1]['selection'] = d[0]['selection']
            elif mutation == 6: d[1]['submit'] = d[0]['submit']
            elif mutation == 7: d[0]['sequence'] = False
            elif mutation == 8: d[0]['selection'] = 12
            elif mutation == 9: f[1]['target'] = 1
            elif mutation == 10: f[1]['frame'] = f[0]['frame']
            elif mutation == 11: f[0]['ready'] = 0
            elif mutation == 12: f[0]['receipt'] = True
            else: d = []
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.eligibility_frames(f, d, c)
        for periods in (True, -1, 4, 1.0, '1'):
            with self.assertRaises(ValueError): module.validate_periods(periods)

    def test_auditor_rejects_future_target_even_if_ready_and_low_age(self):
        f, d, c = population()
        transformed, _ = module.eligibility_frames(f, d, c)
        result = dict(schema=1, frontier=[dict(skips=0, required_count=3, feasible=True,
            maximum_good=3, certificate=[[1, 1], [2, 2], [3, 3]])])
        with self.assertRaises(ValueError):
            module.audit_target_frontier(result, f, transformed, d, c, 0, 100, {1:10,2:20,3:30})
        result['frontier'][0]['certificate'] = [[1, 0], [2, 1], [3, 2]]
        audited = module.audit_target_frontier(result, f, transformed, d, c, 0, 100, {1:10,2:20,3:30})
        self.assertTrue(audited['frontier'][0]['direct_target_admission_checked'])
        bad = copy.deepcopy(transformed)
        bad[1]['ready'] = 2
        with self.assertRaises(ValueError):
            module.audit_target_frontier(result, f, bad, d, c, 0, 100, {1:10,2:20,3:30})

    def test_original_runtime_inputs_and_overflow_reject(self):
        for target, period, prior in ((True, 12, 1), (100, -1, 1), (100, 12, 101), (2**63-1, 12, 2**63-13)):
            with self.assertRaises(ValueError):
                module.target_ceilings([dict(predicted_display_xr_ns=target,
                    predicted_display_period_xr_ns=period, previous_predicted_display_xr_ns=prior)], [dict(sequence=1)], 1)
