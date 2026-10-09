import copy
import importlib.util
from pathlib import Path
import unittest

from test_candidate_trace import bounded_fixture, fallback_after

ROOT = Path(__file__).resolve().parents[3]
path = ROOT / 'prototypes/vr444/experiments/selection_policy/counterfactual.py'
spec = importlib.util.spec_from_file_location('counterfactual_test', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def fixture():
    r, p = bounded_fixture()
    r.update(selection_policy='closest-target', reason='closest_target', selected=copy.deepcopy(r['closest']), chosen_slots=[0, 0, -1])
    p.update(selection_policy='closest-target', left=1, right=1, left_warp=11, right_warp=11)
    return [r], [p]


def sources(same_app=False, clock_offset=0, frames=(1, 2)):
    def event(name, **values):
        r = dict.fromkeys(module.source.INTS, 0)
        r.update(dict.fromkeys(module.source.BOOLS, False))
        r.update(type='event', event=name, app_frame_id=-1, system_frame_id=-1,
                 when_ns=1_000_000, observed_ns=1_000_001)
        r.update(values)
        for key in ('when_ns', 'observed_ns', 'display_time_ns', 'wake_up_ns', 'layer_target_ns'):
            if r[key]:
                r[key] += clock_offset
        return r
    records = []
    for app_id in (1,) if same_app else frames:
        app = dict(pacer_id=1, app_frame_id=app_id)
        records += [event('app_predict', **app, display_time_ns=10_000_000, display_period_ns=11_111_111, wake_up_ns=2_000_000),
                    event('app_delivered', **app, when_ns=3_000_000, observed_ns=3_000_001, display_time_ns=10_000_000),
                    event('app_gpu_done', **app, when_ns=4_000_000, observed_ns=4_000_001)]
    for frame in frames:
        records += [event('app_latched', pacer_id=1, app_frame_id=1 if same_app else frame, system_frame_id=frame,
                          when_ns=5_000_000, observed_ns=5_000_001),
                    event('compositor_frame', system_frame_id=frame, when_ns=6_000_000, observed_ns=6_000_000,
                          display_time_ns=11_000_000, display_period_ns=11_111_111, layer_target_ns=10_000_000,
                          layer_count=1, projection_count=1, direct_projection=True, projection_pose_reused=True),
                    event('projection_layer', system_frame_id=frame, when_ns=6_000_000, observed_ns=6_000_000,
                          layer_target_ns=10_000_000)]
    for i, row in enumerate(records, 1):
        row['sequence'] = i
    return [dict(type='header', schema=1, clock='host_monotonic_ns', capacity=131072, diagnostic_only=True),
            *records, dict(type='footer', records=len(records), dropped=0, complete=True)]


class CounterfactualTests(unittest.TestCase):
    def test_fraction_boundary_and_first_period_preserve_guards(self):
        r = fixture()[0][0]
        self.assertEqual(module.choose(r, 1, 1)['selected'][0]['frame'], 2)
        self.assertEqual(module.choose(r, 1, 2)['selected'][0]['frame'], 1)
        for f in (r['history'][1], r['history'][4]):
            f['view_display_time_xr_ns'] = 110
        self.assertEqual(module.choose(r, 1, 2)['selected'][0]['frame'], 2)
        r['previous_predicted_display_xr_ns'] = 0
        self.assertEqual(module.choose(r, 1, 1)['selected'][0]['frame'], 1)

    def test_orphan_alpha_and_regressive_replacements_rejected(self):
        r = fixture()[0][0]
        r['history'][4]['warp'] += 1
        self.assertEqual(module.choose(r, 1, 1)['selected'][0]['frame'], 1)
        r = fixture()[0][0]
        previous = copy.deepcopy(r['history'][1:2])*2 + [dict(decoder=2, present=False)]
        for f in previous[:2]:
            f.update(frame=3, warp=13, view_display_time_xr_ns=150)
        result = module.choose(r, 1, 1, previous)
        self.assertTrue(result['fallback'])
        self.assertEqual(result['selected'], previous)

    def test_invalid_ratios_and_runtime_inputs_fail(self):
        r = fixture()[0][0]
        for ratio in ((1, 0), (2, 1), (1, 1025), (-1, 1), (True, 1), (1, 1.0)):
            with self.assertRaises(ValueError):
                module.choose(r, *ratio)
        for key, value in (('predicted_display_xr_ns', 0), ('predicted_display_period_xr_ns', -1),
                           ('previous_predicted_display_xr_ns', 100)):
            with self.assertRaises(ValueError):
                module.choose(r | {key: value}, 1, 1)

    def test_source_join_distinguishes_new_encoded_frame_from_reused_app(self):
        for reuse in (False, True):
            traces, presented = fixture()
            result = module.evaluate(traces, presented, sources(reuse), fractions=((1, 1),), warmup_ns=0)
            self.assertEqual(result['status'], 'pass')
            for alternative in result['alternatives']:
                self.assertEqual(alternative['counts']['different_encoded_frame'], 1)
                self.assertEqual(alternative['counts']['strictly_advancing_latched_app_set'], int(not reuse))
            for key in ('measured_speedup', 'latency_gate_evaluated', 'promotion_eligible', 'release_ready'):
                self.assertIs(result[key], False)

    def test_host_clock_shift_does_not_change_client_age_hypothesis(self):
        traces, presented = fixture()
        a = module.evaluate(traces, presented, sources(), fractions=((1, 1),), warmup_ns=0)
        b = module.evaluate(traces, presented, sources(clock_offset=9_000_000_000_000), fractions=((1, 1),), warmup_ns=0)
        self.assertEqual(a['alternatives'], b['alternatives'])

    def test_failed_observed_trace_or_missing_source_provenance_blocks_analysis(self):
        traces, presented = fixture()
        for failure in ('trace', 'source_drop', 'source_join', 'source_clock'):
            t, p, s = copy.deepcopy(traces), copy.deepcopy(presented), sources()
            if failure == 'trace':
                t[0]['selected'][0]['warp'] += 1
            elif failure == 'source_drop':
                s[-1]['dropped'] = 1
            elif failure == 'source_join':
                for row in s:
                    if row.get('system_frame_id') == 2:
                        row['system_frame_id'] = 3
            else:
                s[0]['clock'] = 'client'
            with self.assertRaises(ValueError):
                module.evaluate(t, p, s, fractions=((1, 1),), warmup_ns=0)

    def test_repeated_hypothetical_choice_does_not_claim_full_observed_cadence(self):
        traces, presented = fixture()
        r2, p2 = fallback_after(traces[0], presented[0])
        traces.append(r2)
        presented.append(p2)
        result = module.evaluate(traces, presented, sources(), fractions=((1, 1),), warmup_ns=0)
        recurrent = result['alternatives'][1]
        self.assertEqual(recurrent['counts']['warm_first_hypothetical_frame_submissions'], 1)
        self.assertIsNone(recurrent['conditional_unique_frame_cadence_hz'])

    def test_duplicate_horizon_empty_warmup_and_bad_declarations_fail(self):
        traces, presented = fixture()
        for fractions in ((), ((1, 1), (1, 1)), ((True, 1),), ((1,),)):
            with self.assertRaises(ValueError):
                module.evaluate(traces, presented, sources(), fractions=fractions, warmup_ns=0)
        with self.assertRaises(ValueError):
            module.evaluate(traces, presented, sources())

    def test_pluggable_selector_cannot_invent_metadata_or_bypass_horizon(self):
        traces, presented = fixture()
        for mutation in ('timestamp', 'decoder', 'slots', 'horizon', 'fallback', 'support'):
            def invalid(record, n, d, previous=None):
                result = copy.deepcopy(module.choose(record, n, d, previous))
                if mutation == 'timestamp':
                    result['selected'][0]['receive_xr_ns'] += 1
                elif mutation == 'decoder':
                    result['selected'][0]['decoder'] = 1
                elif mutation == 'slots':
                    result['chosen_slots'] = [0, 0, -1]
                elif mutation == 'horizon':
                    result['max_ahead_xr_ns'] += 1
                elif mutation == 'fallback':
                    result['fallback'] = True
                else:
                    result['supporting_left_slots'] = [2]
                return result
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                module.evaluate(traces, presented, sources(), fractions=((1, 1),), warmup_ns=0, selector=invalid)

