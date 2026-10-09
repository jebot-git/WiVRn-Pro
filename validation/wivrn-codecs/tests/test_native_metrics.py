import copy
import importlib.util
import math
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('native_metrics', Path(__file__).resolve().parents[1]/'integration/native_metrics.py')
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def row(frame=1, submit=1_000_000_000, warp=11):
    return dict(left=frame, right=frame, left_warp=warp, right_warp=warp,
                submit_xr_ns=submit, left_receive_xr_ns=submit-10_000_000,
                right_receive_xr_ns=submit-8_000_000,
                left_decode_ready_xr_ns=submit-3_000_000,
                right_decode_ready_xr_ns=submit-2_000_000,
                predicted_display_xr_ns=submit+10_000_000)


def perf(frame=7, value=2.5, updated=1_000_000_000, session=100):
    return dict(version=1, session=session, updated_ns=updated,
                streams=[dict(id=0, encode_ms=value, stage_frames=dict(encode=frame, decode=999),
                              decode_ms=999, frames=10000)])


class PresentMetrics(unittest.TestCase):
    def test_first_unique_pair_only_and_local_interval_cadence(self):
        first = row()
        repeat = dict(first, submit_xr_ns=1_010_000_000, left_repeat_count=200, right_repeat_count=3)
        result = metrics.evaluate_present([first, repeat, row(2, 1_020_000_000)])
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['unique_stereo_pairs'], 2)
        self.assertEqual(result['presentation_count'], 3)
        self.assertEqual(result['repeat_presentations'], 1)
        self.assertEqual(result['unique_stereo_submission_hz'], 50)
        self.assertEqual(result['pooled']['receive_to_submit_ms']['count'], 4)
        self.assertEqual(result['pooled']['receive_to_submit_ms']['p95'], 10)
        self.assertEqual(result['per_eye']['left']['receive_to_ready_ms']['p50'], 7)
        self.assertEqual(result['per_eye']['right']['ready_to_submit_ms']['p50'], 2)
        self.assertFalse(result['quality']['pixel_quality_verified'])

    def test_exact_two_second_warmup_and_repeat_from_before_window(self):
        first = row()
        before = row(2, 2_999_000_000)
        repeat = dict(before, submit_xr_ns=3_000_000_000)
        after = row(3, 3_010_000_000)
        last = row(4, 3_020_000_000)
        result = metrics.evaluate_present([first, before, repeat, after, last])
        steady = result['after_warmup']
        self.assertEqual(steady['window_start_xr_ns'], 3_000_000_000)
        self.assertEqual(steady['presentation_count'], 3)
        self.assertEqual(steady['unique_stereo_pairs'], 2)
        self.assertEqual(steady['repeat_presentations'], 1)
        self.assertEqual(steady['unique_pair_interval_s'], .01)
        self.assertEqual(steady['unique_stereo_submission_hz'], 100)
        self.assertEqual(result['all_session']['unique_stereo_pairs'], 4)

    def test_single_sample_has_no_fabricated_cadence_or_steady_state(self):
        result = metrics.evaluate_present([row()])
        self.assertEqual(result['status'], 'pass')
        self.assertIsNone(result['unique_stereo_submission_hz'])
        self.assertIsNone(result['unique_pair_interval_s'])
        self.assertEqual(result['after_warmup']['status'], 'unavailable')
        self.assertIsNone(result['after_warmup']['pooled']['receive_to_ready_ms'])

    def test_empty_or_wrong_container_fails(self):
        for rows in ([], None, {}, 'text'):
            with self.subTest(rows=rows):
                self.assertEqual(metrics.evaluate_present(rows)['status'], 'fail')

    def test_mismatched_frame_warp_and_changed_repeat_identity_fail(self):
        for field, value in [('right', 2), ('right_warp', 12)]:
            bad = dict(row(), **{field: value})
            self.assertEqual(metrics.evaluate_present([bad])['status'], 'fail')
        for field, value in [('left_warp', 99), ('left_receive_xr_ns', 980_000_000), ('right_decode_ready_xr_ns', 999_000_000)]:
            repeat = dict(row(), submit_xr_ns=1_010_000_000, **{field: value})
            result = metrics.evaluate_present([row(), repeat])
            self.assertEqual(result['status'], 'fail')
            self.assertEqual(result['rejected_rows'], 1)

    def test_strict_clock_schema_finite_positive_and_ordered(self):
        cases = [(key, value) for key in ('submit_xr_ns', 'left_receive_xr_ns', 'right_decode_ready_xr_ns')
                 for value in (0, -1, True, 1.5, float('nan'), float('inf'), 2**63, '100')]
        cases += [('left_receive_xr_ns', 999_000_000), ('left_decode_ready_xr_ns', 1_000_000_001)]
        for key, value in cases:
            with self.subTest(key=key, value=value):
                result = metrics.evaluate_present([dict(row(), **{key: value})])
                self.assertEqual(result['status'], 'fail')
        for key in ('left', 'right_warp', 'left_receive_xr_ns'):
            bad = row(); del bad[key]
            self.assertEqual(metrics.evaluate_present([bad])['status'], 'fail')

    def test_future_predicted_display_is_not_measured_photon_time(self):
        result = metrics.evaluate_present([dict(row(), predicted_display_xr_ns=9_000_000_000)])
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['per_eye']['left']['receive_to_submit_ms']['max'], 10)

    def test_clock_and_frame_regression_are_not_sorted_away(self):
        for bad in [row(2, 1_000_000_000), row(2, 999_000_000), row(0, 1_010_000_000)]:
            with self.subTest(bad=bad):
                self.assertEqual(metrics.evaluate_present([row(), bad])['status'], 'fail')

    def test_invalid_warmup_is_reported_without_exception(self):
        for value in (True, -1, float('nan'), float('inf'), 10**1000, None):
            with self.subTest(value=str(value)[:20]):
                self.assertEqual(metrics.evaluate_present([row()], value)['status'], 'fail')


class PerformanceMetrics(unittest.TestCase):
    def test_dedup_uses_session_stream_stage_frame_not_snapshot_frames(self):
        a = perf()
        b = perf(updated=1_200_000_000)
        b['streams'][0]['frames'] = 20000
        c = perf(frame=8, value=3, updated=1_400_000_000)
        c['streams'].append(dict(id=1, encode_ms=4, stage_frames=dict(encode=7)))
        result = metrics.evaluate_performance([a, b, c])
        self.assertEqual(result['status'], 'sampled')
        self.assertEqual(result['unique_snapshots'], 3)
        self.assertEqual(result['unique_encode_stage_frames'], 3)
        self.assertEqual(result['repeated_stage_observations'], 1)
        self.assertEqual(result['per_stream']['0']['unique_encode_stage_frames'], 2)
        self.assertEqual(result['encode_ms']['max'], 4)
        self.assertNotIn('decode_ms', result)

    def test_empty_null_stages_and_duplicate_snapshot_are_not_extra_frames(self):
        self.assertEqual(metrics.evaluate_performance([])['status'], 'unavailable')
        null = perf(frame=None, value=None)
        self.assertEqual(metrics.evaluate_performance([null])['status'], 'unavailable')
        result = metrics.evaluate_performance([perf(), perf()])
        self.assertEqual(result['unique_snapshots'], 1)
        self.assertEqual(result['unique_encode_stage_frames'], 1)

    def test_snapshot_and_stage_regressions_fail(self):
        for bad in (perf(frame=6, updated=1_200_000_000), perf(frame=8, updated=999_000_000)):
            self.assertEqual(metrics.evaluate_performance([perf(), bad])['status'], 'invalid')

    def test_inconsistent_duplicate_stage_is_not_silently_averaged(self):
        result = metrics.evaluate_performance([perf(), perf(value=3, updated=1_200_000_000)])
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['unique_encode_stage_frames'], 1)

    def test_session_change_is_explicit_failure_not_merged_run(self):
        result = metrics.evaluate_performance([perf(), perf(session=101)])
        self.assertEqual(result['status'], 'invalid')
        self.assertEqual(result['sessions'], [100, 101])

    def test_invalid_schemas_and_nonfinite_or_boolean_values_fail(self):
        for bad in (None, {}, {'version': True}, 'text'):
            self.assertEqual(metrics.evaluate_performance([bad])['status'], 'invalid')
        for value in (True, -1, float('nan'), float('inf'), 2001, 10**1000, None):
            with self.subTest(value=str(value)[:20]):
                self.assertEqual(metrics.evaluate_performance([perf(value=value)])['status'], 'invalid')
        for frame in (True, -1, 1.2, '7'):
            self.assertEqual(metrics.evaluate_performance([perf(frame=frame)])['status'], 'invalid')

    def test_duplicate_stream_in_snapshot_fails(self):
        bad = perf()
        bad['streams'].append(copy.deepcopy(bad['streams'][0]))
        self.assertEqual(metrics.evaluate_performance([bad])['status'], 'invalid')


if __name__ == '__main__':
    unittest.main()
