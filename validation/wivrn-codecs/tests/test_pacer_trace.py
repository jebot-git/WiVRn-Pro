import copy
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('pacer_timing_analyze', ROOT / 'prototypes/vr444/experiments/pacer_timing/analyze.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def rows():
    return [
        dict(type='header', schema=1, clock='host_monotonic_ns', capacity=65536,
             diagnostic_only=True, coverage='predict_calls_until_close'),
        dict(type='prediction', sequence=1, pacer_id=1, frame_id=0, now_ns=1_000_000_000,
             previous_client_render_ns=0, period_ns=10_000_000, wake_to_present_ns=2_000_000,
             safe_decode_ns=20_000_000, render_to_display_ns=8_000_000, margin_ns=3_000_000,
             slop_ns=500_000, client_render_ns=1_020_000_000, desired_present_ns=1_000_000_000,
             wake_up_ns=1_001_000_000, display_ns=1_028_000_000),
        dict(type='footer', records=1, dropped=0, complete=True)]


class PacerTraceTests(unittest.TestCase):
    def test_valid_and_floor_residual_is_not_declared_failure(self):
        result = module.evaluate(rows())
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['client_render_minus_budget_target_ms']['p95'], -2)
        self.assertTrue(result['promotion_blocked'])

    def test_schema_and_field_types_are_exact(self):
        for key in rows()[1]:
            altered = rows()
            altered[1][key] = True
            self.assertEqual(module.evaluate(altered)['status'], 'fail', key)
            altered = rows()
            del altered[1][key]
            self.assertEqual(module.evaluate(altered)['status'], 'fail', key)
        for key, value in [('schema', True), ('diagnostic_only', 1), ('capacity', 0),
                           ('clock', 'client'), ('coverage', 'all_process_calls')]:
            altered = rows(); altered[0][key] = value
            self.assertEqual(module.evaluate(altered)['status'], 'fail', key)
        altered = rows(); altered[1]['extra'] = 0
        self.assertEqual(module.evaluate(altered)['status'], 'fail')

    def test_all_outputs_and_native_constants_are_replayed(self):
        for key in ('client_render_ns', 'desired_present_ns', 'wake_up_ns', 'display_ns', 'margin_ns', 'slop_ns'):
            altered = rows(); altered[1][key] += 1
            self.assertEqual(module.evaluate(altered)['status'], 'fail', key)

    def test_missing_tail_overflow_and_drops_fail(self):
        for key, value in [('records', 2), ('records', True), ('dropped', 1), ('complete', 1), ('complete', False)]:
            altered = rows(); altered[-1][key] = value
            self.assertEqual(module.evaluate(altered)['status'], 'fail', key)
        self.assertEqual(module.evaluate(rows()[:-1])['status'], 'fail')
        altered = rows(); altered[1]['now_ns'] = module.I64_MAX
        self.assertEqual(module.evaluate(altered)['status'], 'fail')
        for key in ('period_ns', 'safe_decode_ns', 'wake_to_present_ns', 'render_to_display_ns'):
            altered = rows(); altered[1][key] = -1
            self.assertEqual(module.evaluate(altered)['status'], 'fail', key)

    def test_instances_keep_independent_identity_and_first_prediction(self):
        values = rows()
        second = copy.deepcopy(values[1]); second.update(sequence=2, pacer_id=2)
        values.insert(2, second); values[-1]['records'] = 2
        self.assertEqual(module.evaluate(values)['status'], 'pass')
        values[2]['pacer_id'] = 1
        self.assertEqual(module.evaluate(values)['status'], 'fail')
        values = rows(); values[1]['frame_id'] = 1
        self.assertEqual(module.evaluate(values)['status'], 'fail')
        values = rows(); values[1]['previous_client_render_ns'] = 10_000_000
        self.assertEqual(module.evaluate(values)['status'], 'fail')

    def test_feedback_phase_changes_do_not_require_prior_target_equality(self):
        values = rows()
        second = copy.deepcopy(values[1]); second.update(sequence=2, frame_id=1,
                now_ns=1_010_000_000, previous_client_render_ns=1_020_100_000,
                client_render_ns=1_030_100_000, desired_present_ns=1_010_100_000,
                wake_up_ns=1_011_100_000, display_ns=1_038_100_000)
        values.insert(2, second); values[-1]['records'] = 2
        self.assertEqual(module.evaluate(values)['status'], 'pass')
        second['now_ns'] = 999_999_999
        self.assertEqual(module.evaluate(values)['status'], 'fail')


if __name__ == '__main__':
    unittest.main()
