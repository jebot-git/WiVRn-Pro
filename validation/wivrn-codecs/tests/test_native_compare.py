"""Guard against cross-mode configuration leakage and insufficient comparison evidence."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('native_compare', Path(__file__).resolve().parents[1]/'integration/native_compare.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


class NativeComparisonConfiguration(unittest.TestCase):
    def args(self, mode, divider):
        return SimpleNamespace(mode=mode, fps_divider=divider, port=9767, seconds=30,
                               bitrate=50_000_000, q=8, compression='lz4')

    def test_all_modes_and_cadences_share_isolated_scene_and_comparison_optout(self):
        inherited = {'PATH': '/usr/bin', 'WIVRN_CODEC_COMPARE': '0',
                     'WIVRN_CODEC_TEST': 'hevc-carrier', 'WIVRN_CODEC_TEST_FRAME_STRIDE': '12',
                     'WIVRN_CODEC_TEST_UNKNOWN': 'stale', 'WIVRN_CODEC_TEST_COMPRESSION': 'none',
                     'VR444_KERNEL': 'coop64', 'VR444_DEVICE': '9',
                     'WIVRN_DUMP_VIDEO': '/stale/video', 'XDG_RUNTIME_DIR': '/production',
                     'XR_RUNTIME_JSON': '/production/runtime.json'}
        original_environment = dict(os.environ)
        with patch.dict(os.environ, inherited, clear=True):
            for mode in compare.MODES:
                for divider in (1, 2):
                    with self.subTest(mode=mode, divider=divider):
                        server, client, env = compare.build_configuration(
                            self.args(mode, divider), Path('/isolated'), Path('/source'), Path('/output'))
                        self.assertEqual(env['WIVRN_CODEC_COMPARE'], '1')
                        self.assertEqual(env['XDG_RUNTIME_DIR'], '/isolated')
                        self.assertEqual(env['XDG_CONFIG_HOME'], '/isolated/config')
                        self.assertEqual(env['XR_RUNTIME_JSON'], '/source/build/codec-server/openxr_wivrn-dev.json')
                        self.assertEqual(server['publish-service'], 'none')
                        self.assertTrue(server['tcp-only'])
                        self.assertFalse(server['wayvr']['enabled'])
                        self.assertEqual(server['application'][-4:], ['-bm', 'Opaque', '-s', 'Local'])
                        self.assertEqual(client['preferred_refresh_rate'], 90)
                        self.assertEqual(client['fps_divider'], divider)
                        self.assertEqual(client['resolution_scale'], 1.0)
                        self.assertEqual(client['stream_scale'], .3)
                        self.assertFalse(client['eye_gaze'])
                        self.assertEqual(client['openxr_post_processing'], {'super_sampling': 0, 'sharpening': 0})
                        self.assertNotIn('WIVRN_CODEC_TEST_UNKNOWN', env)
                        if mode == 'custom-vr444':
                            self.assertEqual(server['encoder']['encoder'], 'codec-test')
                            self.assertNotIn('codec', server['encoder'])
                            self.assertEqual(env['WIVRN_CODEC_TEST'], 'vulkan-vr444')
                            self.assertEqual(env['WIVRN_CODEC_TEST_FRAME_STRIDE'], '1')
                            self.assertEqual(env['WIVRN_CODEC_TEST_COMPRESSION'], 'lz4')
                            self.assertEqual(env['VR444_KERNEL'], 'baseline')
                            self.assertEqual(env['VR444_DEVICE'], '0')
                            self.assertNotIn('WIVRN_DUMP_VIDEO', env)
                        else:
                            self.assertFalse(any(key.startswith('WIVRN_CODEC_TEST') for key in env))
                            self.assertNotIn('VR444_KERNEL', env)
                            self.assertNotIn('VR444_DEVICE', env)
                            self.assertEqual(env['WIVRN_DUMP_VIDEO'], '/output/host/video')
                            self.assertEqual(server['encoder'], {'encoder': 'qsv', 'device': '/dev/dri/renderD129',
                                                                'codec': 'h264' if mode == 'native-h264' else 'h265'})
                            self.assertEqual(server['bit-depth'], 8)
                            self.assertTrue(client['decoder_low_latency'])
        # Configuration building must not mutate the caller's inherited environment.
        self.assertEqual(dict(os.environ), original_environment)


class NativeComparisonGates(unittest.TestCase):
    def test_experimental_controls_are_explicit_and_backed_up(self):
        from argparse import Namespace
        args = Namespace(mode='custom-vr444', decoder_backend='cpu-neon', cpu_output='mapped-direct',
                         selection_policy='bounded-newest', candidate_trace=True)
        settings = compare.experimental_settings(args)
        self.assertEqual(settings['codec-test-cpu-output.txt'], 'mapped-direct')
        self.assertTrue(set(settings).issubset(compare.CONFIG_FILES))
        for change in (dict(mode='native-hevc'), dict(decoder_backend='resident'), dict(candidate_trace=False),
                       dict(cpu_output='invalid'), dict(selection_policy='invalid')):
            with self.assertRaises(ValueError):
                compare.experimental_settings(Namespace(**(vars(args) | change)))

    def test_inherited_source_trace_is_removed_and_only_explicitly_enabled(self):
        from argparse import Namespace
        args = Namespace(mode='custom-vr444', seconds=30, port=9767, fps_divider=1,
                         bitrate=20_000_000, q=8, compression='lz4', source_trace=False)
        with patch.dict(compare.os.environ, {'WIVRN_CODEC_SOURCE_TRACE': '/unrelated'}):
            _,_,env = compare.build_configuration(args, Path('/runtime'), Path('/source'), Path('/result'))
            self.assertNotIn('WIVRN_CODEC_SOURCE_TRACE',env)
            args.source_trace=True
            _,_,env = compare.build_configuration(args, Path('/runtime'), Path('/source'), Path('/result'))
            self.assertEqual(env['WIVRN_CODEC_SOURCE_TRACE'], '/result/host/source-trace.jsonl')

    def test_source_footer_wait_never_accepts_partial_or_false_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'trace.jsonl'
            self.assertFalse(compare.source_trace_complete(path))
            for text in ('', '{}', '{"type":"footer","complete":1}',
                         '{"type":"footer","complete":false}', '{"type":"footer","complete":'):
                path.write_text(text)
                self.assertFalse(compare.source_trace_complete(path))
            path.write_text('x'*5000+'\n'+json.dumps({'type':'footer','complete':True})+'\n')
            self.assertTrue(compare.source_trace_complete(path))

    def presentations(self, count=30, spacing=100_000_000):
        rows = []
        for frame in range(count):
            submit = 1_000_000_000 + frame*spacing
            rows.append(dict(left=frame, right=frame, left_warp=frame+100, right_warp=frame+100,
                             submit_xr_ns=submit, left_receive_xr_ns=submit-10_000_000,
                             right_receive_xr_ns=submit-9_000_000,
                             left_decode_ready_xr_ns=submit-3_000_000,
                             right_decode_ready_xr_ns=submit-2_000_000))
        return rows

    def evaluate(self, rows, performance=None):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'client').mkdir()
            (root/'client/codec-test-present.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
            if performance is not None:
                (root/'performance-samples.jsonl').write_text(json.dumps({'performance': performance})+'\n')
            # This offline evaluation must not try to run a process or touch ADB.
            with patch.object(compare.subprocess, 'run', side_effect=AssertionError('unexpected subprocess')), \
                 patch.object(compare.subprocess, 'Popen', side_effect=AssertionError('unexpected process')):
                return compare.evaluate_output(root)

    def test_minimum_30_pairs_and_positive_postwarmup_window_pass_without_optional_feed(self):
        result = self.evaluate(self.presentations())
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['presentation']['unique_stereo_pairs'], 30)
        self.assertEqual(result['presentation']['after_warmup']['unique_stereo_pairs'], 10)
        self.assertEqual(result['performance']['status'], 'unavailable')
        self.assertEqual(result['quality']['status'], 'not_measured')

    def test_29_pairs_fail_even_with_usable_postwarmup_interval(self):
        result = self.evaluate(self.presentations(29))
        self.assertEqual(result['presentation']['status'], 'pass')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('fewer than 30 unique coherent stereo pairs', result['comparison_gate_errors'])

    def test_many_startup_pairs_cannot_substitute_for_postwarmup_cadence(self):
        result = self.evaluate(self.presentations(30, 10_000_000))
        self.assertEqual(result['presentation']['status'], 'pass')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('insufficient post-warmup cadence interval', result['comparison_gate_errors'])

    def test_repeated_presentations_do_not_satisfy_unique_pair_minimum(self):
        rows = self.presentations(1)
        rows += [dict(rows[0], submit_xr_ns=1_000_000_000+i*100_000_000) for i in range(1, 35)]
        result = self.evaluate(rows)
        self.assertEqual(result['presentation']['unique_stereo_pairs'], 1)
        self.assertEqual(result['presentation']['repeat_presentations'], 34)
        self.assertEqual(result['status'], 'fail')

    def test_invalid_optional_feed_fails_even_with_valid_presentation_timing(self):
        result = self.evaluate(self.presentations(), {'version': True, 'session': 1, 'updated_ns': 2, 'streams': []})
        self.assertEqual(result['presentation']['status'], 'pass')
        self.assertEqual(result['performance']['status'], 'invalid')
        self.assertEqual(result['status'], 'fail')
        self.assertIn('invalid performance feed; encode samples cannot support comparison', result['comparison_gate_errors'])

    def test_identity_failure_is_not_overridden_by_sufficient_counts(self):
        rows = self.presentations(31)
        rows[-1]['right_warp'] += 1
        result = self.evaluate(rows)
        self.assertEqual(result['presentation']['unique_stereo_pairs'], 30)
        self.assertEqual(result['status'], 'fail')
        self.assertTrue(result['presentation']['errors'])


if __name__ == '__main__':
    unittest.main()
