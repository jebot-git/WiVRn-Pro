import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    'gaze_metrics', Path(__file__).resolve().parents[1] / 'integration/gaze_metrics.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
metrics = module.gaze_timing_metrics


def sample(**changes):
    row = dict(schema=1, sequence=1, scheduled_poll_xr_ns=950_000_000,
               poll_begin_xr_ns=1_000_000_000, poll_end_xr_ns=1_000_100_000,
               requested_pose_xr_ns=1_050_000_000,
               returned_pose_expression_xr_ns=1_050_000_000,
               locate_result=0, location_flags=15, output_pose_flags=51,
               reference='view', time_available=True, expression_after_poll=True,
               orientation_valid=True, position_valid=True,
               orientation_tracked=True, position_tracked=True,
               poll_order_valid=True,
               time_semantics='pose_expression_not_camera_acquisition',
               clock_domain='local_openxr')
    row.update(changes)
    return row


class GazeMetrics(unittest.TestCase):
    def test_off_missing_and_unrequested_collection(self):
        self.assertEqual(metrics([], False)['status'], 'off')
        self.assertEqual(metrics([], True)['status'], 'unverified')
        self.assertEqual(metrics([sample()], False)['status'], 'invalid')

    def test_future_time_is_valid_expression_time(self):
        result = metrics([sample()], True)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['future_expression_time_samples'], 1)
        self.assertEqual(result['usable_expression_time_samples'], 1)
        self.assertEqual(result['poll_duration_ns']['max'], 100_000)
        self.assertFalse(any('age' in key or 'latency' in key for key in result))

    def test_clamped_past_time_is_valid(self):
        row = sample(returned_pose_expression_xr_ns=900_000_000, expression_after_poll=False)
        self.assertEqual(metrics([row], True)['status'], 'pass')

    def test_zero_time_never_qualifies_alone(self):
        row = sample(returned_pose_expression_xr_ns=0, time_available=False, expression_after_poll=False)
        result = metrics([row], True)
        self.assertEqual(result['status'], 'unverified')
        self.assertEqual(result['unavailable_time_samples'], 1)

    def test_failed_locate_is_not_valid_even_with_raw_flags(self):
        row = sample(locate_result=-1, output_pose_flags=0, time_available=False,
                     expression_after_poll=False, orientation_valid=False,
                     position_valid=False, orientation_tracked=False, position_tracked=False)
        self.assertEqual(metrics([row], True)['status'], 'unverified')
        row['time_available'] = True
        self.assertEqual(metrics([row], True)['status'], 'invalid')

    def test_orientation_valid_is_required_not_position_or_tracking(self):
        row = sample(location_flags=1, output_pose_flags=1, position_valid=False,
                     orientation_tracked=False, position_tracked=False)
        self.assertEqual(metrics([row], True)['status'], 'pass')
        row.update(location_flags=4, output_pose_flags=16, orientation_valid=False,
                   orientation_tracked=True)
        self.assertEqual(metrics([row], True)['status'], 'unverified')

    def test_sequence_gaps_valid_repeats_and_regressions_invalid(self):
        later = sample(sequence=21, poll_begin_xr_ns=1_100_000_000,
                       poll_end_xr_ns=1_100_100_000, expression_after_poll=False)
        self.assertEqual(metrics([sample(), later], True)['status'], 'pass')
        for sequence in (1, 0):
            self.assertEqual(metrics([sample(), dict(later, sequence=sequence)], True)['status'], 'invalid')

    def test_order_and_rate(self):
        for changes in (
            dict(poll_end_xr_ns=999_000_000, poll_order_valid=False),
            dict(poll_order_valid=False),
        ):
            self.assertEqual(metrics([sample(**changes)], True)['status'], 'invalid')
        later = sample(sequence=2, poll_begin_xr_ns=1_099_999_999,
                       poll_end_xr_ns=1_100_000_000, expression_after_poll=False)
        self.assertEqual(metrics([sample(), later], True)['status'], 'invalid')

    def test_types_and_ranges_are_strict(self):
        cases = [('schema', True), ('sequence', True), ('sequence', 1 << 64),
                 ('poll_begin_xr_ns', True), ('poll_end_xr_ns', 1.0),
                 ('requested_pose_xr_ns', 1 << 63), ('locate_result', None),
                 ('location_flags', -1), ('output_pose_flags', 64),
                 ('time_available', 1), ('orientation_valid', 'true'),
                 ('clock_domain', 'host'), ('reference', []),
                 ('time_semantics', 'camera_acquisition')]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                self.assertEqual(metrics([sample(**{field: value})], True)['status'], 'invalid')
        self.assertEqual(metrics([None], True)['status'], 'invalid')
        self.assertEqual(metrics(None, True)['status'], 'invalid')
        self.assertEqual(metrics([], 1)['status'], 'invalid')
        row = sample()
        del row['orientation_valid']
        self.assertEqual(metrics([row], True)['status'], 'invalid')

    def test_false_boolean_claims_are_rejected(self):
        for name in ('time_available', 'expression_after_poll', 'orientation_valid',
                     'position_valid', 'orientation_tracked', 'position_tracked'):
            self.assertEqual(metrics([sample(**{name: False})], True)['status'], 'invalid')

    def test_world_transform_can_reduce_validity_but_not_invent_it(self):
        self.assertEqual(metrics([sample(reference='world', output_pose_flags=1)], True)['status'], 'pass')
        self.assertEqual(metrics([sample(reference='view', output_pose_flags=1)], True)['status'], 'invalid')
        self.assertEqual(metrics([sample(reference='world', output_pose_flags=31)], True)['status'], 'invalid')
        # In view space, velocity validity is independent of raw location flags.
        self.assertEqual(metrics([sample(output_pose_flags=63)], True)['status'], 'pass')
        self.assertEqual(metrics([sample(output_pose_flags=15)], True)['status'], 'invalid')

    def test_cap_and_mixed_missing_time(self):
        self.assertEqual(metrics([sample()] * 6001, True)['status'], 'invalid')
        zero = sample(returned_pose_expression_xr_ns=0, time_available=False, expression_after_poll=False)
        later = sample(sequence=2, poll_begin_xr_ns=1_100_000_000,
                       poll_end_xr_ns=1_100_100_000, expression_after_poll=False)
        self.assertEqual(metrics([zero, later], True)['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
