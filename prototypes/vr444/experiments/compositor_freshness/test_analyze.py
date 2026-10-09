import copy
import unittest
from analyze import evaluate, INTS, BOOLS


def event(name, **kwargs):
    r = dict.fromkeys(INTS, 0)
    r.update(dict.fromkeys(BOOLS, False))
    r.update(type='event', event=name, app_frame_id=-1, system_frame_id=-1,
             when_ns=1_000_000, observed_ns=1_000_001)
    r.update(kwargs)
    return r


def fixture():
    app = dict(pacer_id=1, app_frame_id=1)
    records = [
        event('app_predict', **app, display_time_ns=10_000_000,
              display_period_ns=11_111_111, wake_up_ns=2_000_000),
        event('app_point', **app, when_ns=2_000_000, observed_ns=2_000_001),
        event('app_delivered', **app, when_ns=3_000_000, observed_ns=3_000_001,
              display_time_ns=10_000_000),
        event('app_gpu_done', **app, when_ns=4_000_000, observed_ns=4_000_001),
    ]
    for system in (80, 81):
        records += [event('app_latched', **app, system_frame_id=system,
                          when_ns=5_000_000, observed_ns=5_000_001),
                    event('compositor_frame', system_frame_id=system,
                          when_ns=6_000_000, observed_ns=6_000_000,
                          display_time_ns=11_000_000, display_period_ns=11_111_111,
                          layer_target_ns=10_000_000, layer_count=1,
                          projection_count=1, direct_projection=True,
                          projection_pose_reused=True),
                    event('projection_layer', system_frame_id=system,
                          when_ns=6_000_000, observed_ns=6_000_000,
                          layer_target_ns=10_000_000)]
    for i, r in enumerate(records, 1):
        r['sequence'] = i
    return [dict(type='header', schema=1, clock='host_monotonic_ns',
                 capacity=131072, diagnostic_only=True), *records,
            dict(type='footer', records=len(records), dropped=0, complete=True)]


class AnalyzerTests(unittest.TestCase):
    def test_valid_reused_app_frames(self):
        result = evaluate(fixture(), [80, 81, 81])
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['adjacent_reused_app_sets'], 1)
        self.assertEqual(result['unique_latched_app_sets'], 1)
        self.assertEqual(result['projection_target_matches'], 2)
        self.assertEqual(result['app_delivery_to_compositor_input_ms']['p95'], 3)
        self.assertTrue(result['promotion_blocked'])

    def test_footer_and_overflow_fail_closed(self):
        for field, value in [('dropped', 1), ('complete', False), ('records', 1)]:
            rows = fixture()
            rows[-1][field] = value
            self.assertEqual(evaluate(rows)['status'], 'fail')
        self.assertEqual(evaluate(fixture()[:-1])['status'], 'fail')

    def test_bad_keys_types_clock_and_sequences(self):
        for field, value in [('sequence', 9), ('when_ns', True), ('observed_ns', 0),
                             ('pacer_id', '1'), ('direct_projection', 1)]:
            rows = fixture()
            rows[1][field] = value
            self.assertEqual(evaluate(rows)['status'], 'fail', field)
        rows = fixture()
        rows[0]['clock'] = 'headset'
        self.assertEqual(evaluate(rows)['status'], 'fail')

    def test_malformed_event_name_fails_without_exception(self):
        for value in ({'bad': 'name'}, [], None, 7):
            rows = fixture()
            rows[1]['event'] = value
            self.assertEqual(evaluate(rows)['status'], 'fail')
        rows = fixture()
        rows[0]['capacity'] = 1
        self.assertEqual(evaluate(rows)['status'], 'fail')

    def test_missing_prediction_or_completion(self):
        for index in (1, 3, 4):
            rows = fixture()
            del rows[index]
            for i, r in enumerate(rows[1:-1], 1):
                r['sequence'] = i
            rows[-1]['records'] -= 1
            self.assertEqual(evaluate(rows)['status'], 'fail', index)

    def test_duplicate_latch_or_frame_fails(self):
        for index in (5, 6):
            rows = fixture()
            rows.insert(-1, copy.deepcopy(rows[index]))
            rows[-2]['sequence'] = rows[-1]['records'] + 1
            rows[-1]['records'] += 1
            self.assertEqual(evaluate(rows)['status'], 'fail')

    def test_wrong_projection_target_or_path_fails(self):
        for index, field, value in [(7, 'layer_target_ns', 123),
                                    (6, 'direct_projection', False),
                                    (6, 'projection_pose_reused', False),
                                    (7, 'layer_index', 2),
                                    (7, 'when_ns', 7_000_000)]:
            rows = fixture()
            rows[index][field] = value
            self.assertEqual(evaluate(rows)['status'], 'fail', field)

    def test_selected_frame_join_required(self):
        for selection in ([82], [], [True], [80.0], '80'):
            self.assertEqual(evaluate(fixture(), selection)['status'], 'fail')

    def test_callback_order_and_identity_fail(self):
        for index, field, value in [(5, 'when_ns', 3_999_999),
                                    (5, 'app_frame_id', 2),
                                    (3, 'display_time_ns', 0),
                                    (4, 'when_ns', 2_999_999)]:
            rows = fixture()
            rows[index][field] = value
            self.assertEqual(evaluate(rows)['status'], 'fail', field)

    def test_ambiguous_target_join_is_visible(self):
        rows = fixture()
        app_rows = [copy.deepcopy(r) for r in rows[1:-1] if r['event'].startswith('app_')]
        for r in app_rows:
            r['pacer_id'] = 2
        rows[-1:-1] = app_rows
        for i, r in enumerate(rows[1:-1], 1):
            r['sequence'] = i
        rows[-1]['records'] += len(app_rows)
        result = evaluate(rows)
        self.assertEqual(result['status'], 'pass', result)
        self.assertEqual(result['ambiguous_projection_matches'], 2)

    def test_multiple_layers_cutoff_controls_pose_reuse(self):
        rows = fixture()
        for r in rows:
            if r.get('event') == 'compositor_frame':
                r.update(layer_count=2, direct_projection=False,
                         display_time_ns=100_000_000, projection_pose_reused=False)
        self.assertEqual(evaluate(rows)['status'], 'pass')


if __name__ == '__main__':
    unittest.main()
