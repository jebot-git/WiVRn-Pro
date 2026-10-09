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
                               bitrate=50_000_000, q=8,
                               compression='none' if mode == 'custom-rrb32' else 'lz4',
                               decoder_backend='cpu-neon' if mode == 'custom-rrb32' else 'resident')

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
                        if mode in compare.CUSTOM_MODES:
                            self.assertEqual(server['encoder']['encoder'], 'codec-test')
                            self.assertNotIn('codec', server['encoder'])
                            self.assertEqual(env['WIVRN_CODEC_TEST'], 'rrb32-vr444' if mode == 'custom-rrb32' else 'vulkan-vr444')
                            self.assertEqual(env['WIVRN_CODEC_TEST_FRAME_STRIDE'], '1')
                            self.assertEqual(env['WIVRN_CODEC_TEST_COMPRESSION'], 'none' if mode == 'custom-rrb32' else 'lz4')
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
    def test_longer_visible_window_is_only_an_explicit_quality_application(self):
        from argparse import Namespace
        for seconds in (10, 30):
            self.assertTrue(compare.acquisition_duration_valid(Namespace(seconds=seconds)))
        for seconds in (31, 60, 61, True):
            self.assertFalse(compare.acquisition_duration_valid(Namespace(seconds=seconds)))
        for seconds in (31, 60):
            self.assertTrue(compare.acquisition_duration_valid(Namespace(seconds=seconds,
                quality_application_plan=Path('/plan'), quality_capture=True)))
        for changes in (dict(seconds=61), dict(quality_capture=False), dict(quality_application_plan=None)):
            args=dict(seconds=60, quality_application_plan=Path('/plan'), quality_capture=True)|changes
            self.assertFalse(compare.acquisition_duration_valid(Namespace(**args)))

    def test_quality_capture_bounds_reset_and_source_preflight(self):
        from argparse import Namespace
        args = Namespace(mode='custom-rrb32', quality_capture=True, quality_max_pairs=6, quality_frame_stride=90)
        name = 'codec-test-quality-capture.json'
        self.assertIn(name, compare.CONFIG_FILES)
        self.assertTrue(json.loads(compare.quality_settings(args)[name])['enabled'])
        for changes in (dict(quality_capture=1), dict(quality_max_pairs=True), dict(quality_max_pairs=9),
                        dict(quality_frame_stride=0), dict(quality_frame_stride=91), dict(quality_frame_stride=901), dict(mode='native-hevc')):
            with self.assertRaises(ValueError): compare.quality_settings(Namespace(**(vars(args) | changes)))
        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaisesRegex(ValueError, 'capture source'):
                compare.quality_source_helpers(args, Path(folder))
        args.quality_capture = False
        self.assertFalse(json.loads(compare.quality_settings(args)[name])['enabled'])
        self.assertEqual(compare.quality_source_helpers(args, Path('/absent')), ())

    def test_rrb_unintegrated_sources_stop_before_device_access(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(mode='custom-rrb32', decoder_backend='cpu-neon', seconds=30, port=9767,
                fps_divider=1, bitrate=20_000_000, q=1, compression='none', source_trace=False,
                candidate_trace=False, pacer_trace=False, selection_phase='after-fence',
                cpu_output='cached-copy', selection_policy='closest-target',
                rrb_source='q1', rrb_workers=4, rrb_output='cpu',
                source=Path(folder)/'not-integrated', out=Path(folder)/'unused', serial='not-queried')
            with patch.object(compare, 'command') as device, patch.object(compare.subprocess, 'run') as subprocess:
                with self.assertRaisesRegex(ValueError, 'RRB native source'):
                    compare.run(args)
                device.assert_not_called(); subprocess.assert_not_called()
                self.assertFalse(args.out.exists())

    def test_rrb_modes_require_explicit_payload_workers_output_and_exact_q(self):
        from argparse import Namespace
        args = Namespace(mode='custom-rrb32', decoder_backend='cpu-neon', cpu_output='cached-copy',
                         rrb_source='q1', rrb_workers=4, rrb_output='cpu', q=1, compression='none')
        self.assertEqual(compare.rrb_settings(args)['codec-test-payload.txt'], 'rrb32')
        self.assertTrue(set(compare.rrb_settings(args)).issubset(compare.CONFIG_FILES))
        for changes in (dict(q=8), dict(compression='lz4'), dict(rrb_workers=True),
                        dict(rrb_workers=3), dict(decoder_backend='resident'),
                        dict(cpu_output='mapped-direct'), dict(rrb_output='unknown')):
            with self.assertRaises(ValueError):
                compare.rrb_settings(Namespace(**(vars(args) | changes)))
        args.rrb_source = 'vr444'; args.q = 8; args.rrb_output = 'staged-gpu'
        self.assertEqual(compare.rrb_settings(args)['codec-test-rrb-output.txt'], 'staged-gpu')
        args.mode = 'custom-vr444'
        with self.assertRaises(ValueError): compare.rrb_settings(args)
        args.rrb_output = 'cpu'
        self.assertEqual(compare.rrb_settings(args)['codec-test-payload.txt'], 'vr444')

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
        with patch.dict(compare.os.environ, {'WIVRN_CODEC_SOURCE_TRACE': '/unrelated', 'WIVRN_CODEC_PACER_TRACE': '/hidden'}):
            _,_,env = compare.build_configuration(args, Path('/runtime'), Path('/source'), Path('/result'))
            self.assertNotIn('WIVRN_CODEC_SOURCE_TRACE',env)
            self.assertNotIn('WIVRN_CODEC_PACER_TRACE',env)
            args.source_trace=True
            _,_,env = compare.build_configuration(args, Path('/runtime'), Path('/source'), Path('/result'))
            self.assertEqual(env['WIVRN_CODEC_SOURCE_TRACE'], '/result/host/source-trace.jsonl')

    def test_pacer_diagnostics_require_explicit_combined_controls_and_source_preflight(self):
        from argparse import Namespace
        args = Namespace(mode='custom-vr444', decoder_backend='cpu-neon', seconds=30, port=9767,
                         fps_divider=1, bitrate=20_000_000, q=8, compression='lz4', source_trace=True,
                         candidate_trace=True, pacer_trace=True)
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)
            with self.assertRaisesRegex(ValueError, 'overlay is absent'):
                compare.pacer_trace_helpers(args, source)
            for name in compare.pacer_trace_helpers(args):
                p = source/name; p.parent.mkdir(parents=True, exist_ok=True); p.write_text('test fixture')
            self.assertEqual(len(compare.pacer_trace_helpers(args, source)), 3)
            _, _, env = compare.build_configuration(args, Path('/runtime'), source, Path('/result'))
            self.assertEqual(env['WIVRN_CODEC_PACER_TRACE'], '/result/host/pacer-trace.jsonl')
        for change in (dict(mode='native-hevc'), dict(decoder_backend='resident'), dict(candidate_trace=False),
                       dict(source_trace=False), dict(pacer_trace=1)):
            with self.assertRaises(ValueError):
                compare.pacer_trace_helpers(Namespace(**(vars(args) | change)))
        args.pacer_trace = False
        self.assertEqual(compare.pacer_trace_helpers(args, Path('/nonexistent')), ())

    def test_absent_pacer_overlay_stops_before_any_device_command(self):
        from argparse import Namespace
        with tempfile.TemporaryDirectory() as folder:
            args = Namespace(mode='custom-vr444', decoder_backend='cpu-neon', seconds=30, port=9767,
                fps_divider=1, bitrate=20_000_000, q=8, compression='lz4', source_trace=True,
                candidate_trace=True, pacer_trace=True, selection_phase='early',
                cpu_output='cached-copy', selection_policy='closest-target',
                source=Path(folder)/'not-integrated', out=Path(folder)/'unused', serial='not-queried')
            with patch.object(compare, 'command') as device, patch.object(compare.subprocess, 'run') as subprocess:
                with self.assertRaisesRegex(ValueError, 'overlay is absent'):
                    compare.run(args)
                device.assert_not_called()
                subprocess.assert_not_called()
                self.assertFalse(args.out.exists())

    def test_pacer_diagnostic_file_evaluation_requires_exact_source_join(self):
        from test_pacer_source_join import fixture
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            (output/'host').mkdir(); (output/'client').mkdir()
            self.assertEqual(compare.evaluate_pacer_trace(output, False)['status'], 'off')
            self.assertEqual(compare.evaluate_pacer_trace(output, 1)['status'], 'fail')
            self.assertEqual(compare.evaluate_pacer_trace(output, True)['status'], 'fail')
            p, s = fixture()
            for path, rows in (('host/pacer-trace.jsonl', p), ('host/source-trace.jsonl', s),
                               ('client/codec-test-present.jsonl', [{'left': 0}])):
                (output/path).write_text(''.join(json.dumps(row)+'\n' for row in rows))
            result = compare.evaluate_pacer_trace(output, True)
            self.assertEqual(result['status'], 'pass', result)
            self.assertEqual(len(result['analyzer_sources_sha256']), 3)
            self.assertEqual(compare.evaluate_pacer_trace(output, False)['status'], 'fail')
            (output/'host/pacer-trace.jsonl').write_text('{}\n')
            self.assertEqual(compare.evaluate_pacer_trace(output, True)['status'], 'fail')

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
