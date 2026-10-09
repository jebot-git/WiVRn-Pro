import copy
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from test_latency_runtime import output_evidence

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location('client_timeline_test', ROOT / 'prototypes/vr444/experiments/client_timeline/analyze.py')
module = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(module)


def fixture():
    p, d = output_evidence('cached-copy')
    p[0]['predicted_display_xr_ns'] = 20_000_000
    p[0]['selection_policy'] = 'closest-target'
    provenance = dict(decoder_backend='cpu-neon', cpu_output='cached-copy',
                      selection_phase='after-fence', selection_policy='closest-target', candidate_trace=False)
    return p, d, provenance


class ClientTimelineTests(unittest.TestCase):
    def test_exact_stereo_stages_and_threshold_crossing_partition(self):
        p, d, provenance = fixture()
        r = module.evaluate(p, d, provenance, warmup_ns=0, threshold_ns=6_000_000)
        self.assertEqual(r['first_stereo_submissions'], 1)
        self.assertEqual([r['stage_ms'][key]['p95'] for key in module.STAGES], [3, 4, 2, 1])
        self.assertEqual(r['stage_ms']['total_ns']['p95'], 10)
        self.assertEqual(sum(r['p95_total_ranked_sample']['intervals_ns'].values()), 10_000_000)
        self.assertEqual(r['cumulative_threshold_first_crossing_counts'][module.STAGES[1]], 1)
        self.assertEqual(sum(r['cumulative_threshold_first_crossing_counts'].values()), r['total_at_or_above_threshold'])
        self.assertEqual(r['selected_warm_eye_decoder_ms']['cpu_decode_ms']['count'], 2)
        self.assertIs(r['measured_speedup'], False)
        self.assertIs(r['qualification_changed'], False)

    def test_strict_threshold_exact_boundary_and_stage_crossings(self):
        p, d, provenance = fixture()
        for threshold, index in ((3_000_000, 0), (7_000_000, 1), (9_000_000, 2), (10_000_000, 3)):
            result = module.evaluate(p, d, provenance, warmup_ns=0, threshold_ns=threshold)
            self.assertEqual(result['cumulative_threshold_first_crossing_counts'][module.STAGES[index]], 1)
        result = module.evaluate(p, d, provenance, warmup_ns=0, threshold_ns=10_000_001)
        self.assertEqual(result['total_at_or_above_threshold'], 0)
        self.assertEqual(result['at_or_above_threshold_stage_ms'], {})

    def test_repeat_after_warmup_is_not_new_first_submission(self):
        p, d, provenance = fixture()
        repeat = copy.deepcopy(p[0])
        for key in ('render_fence_begin_xr_ns', 'render_fence_end_xr_ns', 'selection_begin_xr_ns',
                    'selection_end_xr_ns', 'end_frame_return_xr_ns', 'submit_xr_ns', 'predicted_display_xr_ns'):
            repeat[key] += 20_000_000
        p.append(repeat)
        self.assertEqual(module.evaluate(p, d, provenance, warmup_ns=0)['first_stereo_submissions'], 1)
        with self.assertRaisesRegex(ValueError, 'no first submissions'):
            module.evaluate(p, d, provenance, warmup_ns=10_000_000)

    def test_common_client_clock_translation_preserves_intervals(self):
        p, d, provenance = fixture()
        original = module.evaluate(p, d, provenance, warmup_ns=0)
        shifted = copy.deepcopy(p)
        for row in shifted:
            for key in module.runtime._TIMES + ('predicted_display_xr_ns',):
                row[key] += 10**14
        changed = module.evaluate(shifted, d, provenance, warmup_ns=0)
        for key in ('stage_ms', 'prediction_phase_ms', 'cumulative_threshold_first_crossing_counts',
                    'p95_total_ranked_sample', 'selected_warm_eye_decoder_ms'):
            self.assertEqual(original[key], changed[key])

    def test_corrupt_stage_eye_join_and_missing_prediction_rejected(self):
        p, d, provenance = fixture()
        for key, value in (('selection_end_xr_ns', 1), ('right_decode_ready_xr_ns', 9_000_000),
                           ('right', 11), ('predicted_display_xr_ns', True)):
            modified = copy.deepcopy(p); modified[0][key] = value
            with self.assertRaises(ValueError):
                module.evaluate(modified, d, provenance, warmup_ns=0)
        missing = copy.deepcopy(p); missing[0].pop('predicted_display_xr_ns')
        with self.assertRaises(ValueError):
            module.evaluate(missing, d, provenance, warmup_ns=0)
        with self.assertRaises(ValueError):
            module.evaluate(p, d[:1], provenance, warmup_ns=0)
        for change in (dict(warmup_ns=True), dict(warmup_ns=-1), dict(threshold_ns=0)):
            with self.assertRaises(ValueError):
                module.evaluate(p, d, provenance, **change)

    def test_manifest_sealing_and_tampering_fail_closed(self):
        with tempfile.TemporaryDirectory() as name:
            run = Path(name)
            (run / 'client/codec-test').mkdir(parents=True)
            p, d, provenance = fixture()
            # Realistic 3-second offset between distinct frames leaves one warm first.
            p2 = copy.deepcopy(p[0]); p2.update(left=11, right=11)
            for key in module.runtime._TIMES + ('predicted_display_xr_ns',):
                p2[key] += 3_000_000_000
            p.append(p2)
            files = {'provenance.json': json.dumps(provenance), 'summary.json': '{"status":"fail"}',
                     'cleanup.json': '{"restored":true,"cleanup_errors":[]}',
                     'client/codec-test-present.jsonl': ''.join(json.dumps(r)+'\n' for r in p)}
            for eye in (0, 1):
                records = [d[eye], d[eye] | {'frame': 11}, {'type': 'summary', 'queue_dropped': 0}]
                files[f'client/codec-test/decode-{eye}.jsonl'] = ''.join(json.dumps(r)+'\n' for r in records)
            for file, data in files.items():
                (run / file).write_text(data)
            manifest = {file: module.sha(run/file) for file in files}
            (run/'hashes.json').write_text(json.dumps(manifest))
            result = module.analyze_run(run)
            self.assertEqual(result['status'], 'pass')
            self.assertEqual(result['original_run_status'], 'fail')
            self.assertFalse(result['qualification_changed'])
            self.assertEqual(result['first_stereo_submissions'], 1)
            self.assertEqual(result['decoder_terminal_summary_present'], [True, True])
            self.assertFalse(result['decoder_process_tail_verified'])
            for eye in (0, 1):
                path = run/f'client/codec-test/decode-{eye}.jsonl'
                path.write_text('\n'.join(path.read_text().splitlines()[:-1])+'\n')
                manifest[str(path.relative_to(run))] = module.sha(path)
            (run/'hashes.json').write_text(json.dumps(manifest))
            no_tail = module.analyze_run(run)
            self.assertEqual(no_tail['status'], 'pass')
            self.assertEqual(no_tail['decoder_terminal_summary_present'], [False, False])
            self.assertFalse(no_tail['decoder_process_tail_verified'])
            (run/'summary.json').write_text('{"status":"pass"}')
            with self.assertRaisesRegex(ValueError, 'manifest'):
                module.analyze_run(run)
            manifest['summary.json'] = module.sha(run/'summary.json')
            manifest['../escape'] = '0' * 64
            (run/'hashes.json').write_text(json.dumps(manifest))
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                module.analyze_run(run)


if __name__ == '__main__':
    unittest.main()
