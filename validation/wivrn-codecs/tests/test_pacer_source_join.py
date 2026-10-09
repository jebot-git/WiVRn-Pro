import copy
import importlib.util
from pathlib import Path
import unittest
from test_pacer_trace import rows as pacer_rows
from test_selection_counterfactual import sources

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('pacer_source_join_test', ROOT / 'prototypes/vr444/experiments/pacer_timing/join_source.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fixture():
    p = pacer_rows()
    p[1].update(now_ns=1_000_000, wake_to_present_ns=9_000_000, safe_decode_ns=0, render_to_display_ns=1_000_000,
                client_render_ns=10_000_000, desired_present_ns=10_000_000,
                wake_up_ns=4_000_000, display_ns=11_000_000)
    s = sources(frames=(1,))
    for r in s:
        if r.get('system_frame_id', -1) >= 0:
            r['system_frame_id'] = 0
    return p, s


class PacerSourceJoinTests(unittest.TestCase):
    def test_exact_output_target_join_preserves_phase_values(self):
        p, s = fixture()
        result = module.evaluate(p, s, [0])
        self.assertEqual(result['status'], 'pass', result.get('errors'))
        self.assertEqual(result['exactly_joined_frames'], 1)
        values = result['selected_host_budget_and_phase_ms']
        self.assertEqual(values['prediction_to_accepted_input_ns']['p95'], 5)
        self.assertEqual(values['accepted_input_minus_desired_present_ns']['p95'], -4)
        self.assertFalse(result['measured_speedup'])
        self.assertTrue(result['promotion_blocked'])
        # The source observes the current period at commit; a period update
        # between predict and commit does not falsify frame/target identity.
        for r in s:
            if r.get('event') == 'compositor_frame':
                r['display_period_ns'] = 12_000_000
        self.assertEqual(module.evaluate(p, s, [0])['status'], 'pass')

    def test_matching_id_with_wrong_target_or_preceding_prediction_fails(self):
        p, s = fixture()
        p[1]['render_to_display_ns'] += 1
        p[1]['display_ns'] += 1
        result = module.evaluate(p, s, [0])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('0 exact predictor matches', result['errors'][0])
        p, s = fixture(); p[1]['now_ns'] = 7_000_000
        result = module.evaluate(p, s, [0])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('predates its prediction', result['errors'][0])

    def test_duplicate_instance_match_is_ambiguous_and_unaccepted_predictions_are_allowed(self):
        p, s = fixture()
        duplicate = copy.deepcopy(p[1]); duplicate.update(sequence=2, pacer_id=2)
        p.insert(2, duplicate); p[-1]['records'] = 2
        result = module.evaluate(p, s, [0])
        self.assertEqual(result['status'], 'fail')
        self.assertIn('2 exact predictor matches', result['errors'][0])
        p, s = fixture()
        next_prediction = copy.deepcopy(p[1]); next_prediction.update(sequence=2, frame_id=1,
            now_ns=11_000_000, previous_client_render_ns=10_000_000,
            client_render_ns=20_000_000, desired_present_ns=20_000_000,
            wake_up_ns=14_000_000, display_ns=21_000_000)
        p.insert(2, next_prediction); p[-1]['records'] = 2
        result = module.evaluate(p, s, [0])
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['pacer_validation']['records'], 2)
        self.assertEqual(result['exactly_joined_frames'], 1)

    def test_same_host_clock_translation_preserves_durations(self):
        p, s = fixture()
        before = module.evaluate(p, s, [0])
        shift = 10**12  # an integer number of predictor periods
        for key in ('now_ns', 'client_render_ns', 'desired_present_ns', 'wake_up_ns', 'display_ns'):
            p[1][key] += shift
        for r in s[1:-1]:
            for key in ('when_ns', 'observed_ns', 'display_time_ns', 'wake_up_ns', 'layer_target_ns'):
                if r[key]:
                    r[key] += shift
        after = module.evaluate(p, s, [0])
        self.assertEqual(after['status'], 'pass', after.get('errors'))
        self.assertEqual(before['selected_host_budget_and_phase_ms'], after['selected_host_budget_and_phase_ms'])

    def test_missing_selected_frame_false_footer_or_domain_fails(self):
        for failure in ('selected', 'footer', 'domain'):
            p, s = fixture()
            selected = [0]
            if failure == 'selected':
                selected = [1]
            elif failure == 'footer':
                p[-1]['complete'] = 1
            else:
                p[0]['clock'] = 'client'
            self.assertEqual(module.evaluate(p, s, selected)['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
