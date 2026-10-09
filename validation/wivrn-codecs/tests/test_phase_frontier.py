import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('phase_frontier_test', Path(__file__).resolve().parents[3]/'prototypes/vr444/experiments/selection_policy/phase_frontier.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    alternatives = []
    for phase, advance in module.grid((0, 5), (0, 2)):
        good = 19 if phase == 5 else 17
        frontier = [dict(feasible=True, maximum_good=good, required_good_count_for_p95=19,
            required_count=count, p95_under_threshold_attainable_in_model=good >= 19,
            conditional_unique_frame_cadence_hz=90, certificate_sha256=f'{phase}-{advance}-{count}',
            certificate=[[1, 1]], certificate_retained=True) for count in (20, 19)]
        alternatives.append(dict(sampling_shift_ns=phase, hypothetical_publication_advance_ns=advance,
            warm_start=dict(model='observed_submission_prefix'), frontier=frontier,
            highest_count_meeting_conditional_age_and_cadence=20 if good >= 19 else None))
    return dict(alternatives=alternatives, maximum_skips=1, cadence_floor_reference_hz=88, assumptions=[])


class PhaseFrontierTests(unittest.TestCase):
    def test_finite_grid_is_exact_distinct_and_contains_existing_reference_cases(self):
        scenarios = module.grid()
        self.assertEqual(len(scenarios), 125)
        self.assertEqual(len(set(scenarios)), 125)
        self.assertIn((5_555_555, 4_000_000), scenarios)
        self.assertIn((0, 8_000_000), scenarios)
        self.assertIn((11_111_111, 0), scenarios)
        for phases, advances in [((0, 0), (0,)), ((1, 0), (0,)), ((True,), (0,)), ((-1,), (0,)),
                                 ((15_000_001,), (0,)), ((), (0,)), (tuple(range(201)), (0,))]:
            with self.assertRaises(ValueError): module.grid(phases, advances)

    def test_only_audited_declared_cells_produce_conditional_grid_solutions(self):
        result = module.summarize(fixture(), (0, 5), (0, 2))
        self.assertEqual(result['conditionally_feasible_grid_cells'], 2)
        self.assertEqual([row['phase_ns'] for row in result['best_tested_phase_per_advance']], [5, 5])
        self.assertEqual(result['best_tested_phase_per_advance'][0]['highest_conditionally_qualifying_count'], 20)
        self.assertFalse(result['continuous_phase_optimum_proven'])
        self.assertFalse(result['scenarios_exhaustive'])
        self.assertNotIn('certificate', result['alternatives'][0]['frontier'][0])
        self.assertIn('certificate', result['alternatives'][2]['frontier'][0])
        self.assertIn('certificate_sha256', result['alternatives'][0]['frontier'][0])

    def test_age_count_without_cadence_never_becomes_joint_solution(self):
        result = fixture()
        for alternative in result['alternatives']:
            for entry in alternative['frontier']: entry['conditional_unique_frame_cadence_hz'] = 87
            alternative['highest_count_meeting_conditional_age_and_cadence'] = None
        self.assertEqual(module.summarize(result, (0, 5), (0, 2))['conditionally_feasible_grid_cells'], 0)

    def test_omitted_reordered_or_mislabeled_scenarios_fail(self):
        for mutation in range(6):
            result = fixture()
            if mutation == 0: result['alternatives'].pop()
            elif mutation == 1: result['alternatives'].reverse()
            elif mutation == 2: result['alternatives'][0]['sampling_shift_ns'] = False
            elif mutation == 3: result['alternatives'][0]['warm_start']['model'] = 'relaxed_known_universe'
            elif mutation == 4: result['alternatives'][0]['frontier'].pop()
            else: result['alternatives'][0]['highest_count_meeting_conditional_age_and_cadence'] = 20
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.summarize(result, (0, 5), (0, 2))
