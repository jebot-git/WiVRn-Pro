import copy
import importlib.util
import hashlib
import json
import tempfile
from pathlib import Path
import unittest

path = Path(__file__).resolve().parents[1]/'integration/latency_policy.py'
spec = importlib.util.spec_from_file_location('test_latency_policy_module', path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def rows(latency_ms=16, hz=90, count=1200):
    result = []
    for frame in range(count):
        submit = 1_000_000_000 + int(frame*1e9/hz)
        receive = submit-int(latency_ms*1e6)
        result.append(dict(left=frame, right=frame, left_warp=42, right_warp=42,
                           submit_xr_ns=submit, left_receive_xr_ns=receive,
                           right_receive_xr_ns=receive+1_000_000,
                           left_decode_ready_xr_ns=receive+2_000_000,
                           right_decode_ready_xr_ns=receive+3_000_000))
    return result


class LatencyPolicyTests(unittest.TestCase):
    def setUp(self):
        self.provenance = copy.deepcopy(module.load_policy()['profile'])

    def test_native_level_can_pass_but_not_certify_release(self):
        report = module.evaluate(rows(), self.provenance)
        self.assertEqual(report['status'], 'pass')
        self.assertFalse(report['promotion_eligible'])
        self.assertFalse(report['optical_motion_to_photon_verified'])

    def test_uses_earliest_eye_receipt_not_pooled_average(self):
        report = module.evaluate(rows(17.5), self.provenance)
        self.assertEqual(report['observed']['stereo_receive_to_submit_ms_p50'], 17.5)
        self.assertEqual(report['status'], 'fail')

    def test_tail_cannot_hide_behind_good_median(self):
        data = rows()
        for row in data[1000:]:
            row['left_receive_xr_ns'] -= 15_000_000
        report = module.evaluate(data, self.provenance)
        self.assertEqual(report['observed']['stereo_receive_to_submit_ms_p50'], 16)
        self.assertEqual(report['status'], 'fail')

    def test_high_repeated_refresh_cannot_hide_low_unique_cadence(self):
        data = rows(hz=70)
        doubled = []
        for row in data:
            doubled.append(row)
            doubled.append(row | {'submit_xr_ns': row['submit_xr_ns']+5_000_000})
        report = module.evaluate(doubled, self.provenance)
        self.assertEqual(report['status'], 'fail')
        self.assertAlmostEqual(report['observed']['unique_stereo_submission_hz'], 70, places=5)

    def test_insufficient_duration_and_empty_fail(self):
        for data in ([], rows(count=200)):
            self.assertEqual(module.evaluate(data, self.provenance)['status'], 'fail')

    def test_geometry_rate_transport_and_missing_profile_fail(self):
        for key, value in [('geometry', [1152,640]), ('requested_new_content_fps',45),
                           ('fps_divider',True), ('transport','UDP')]:
            with self.subTest(key=key):
                self.assertEqual(module.evaluate(rows(), self.provenance | {key:value})['status'], 'fail')
        self.assertEqual(module.evaluate(rows(), {})['status'], 'fail')

    def test_wrong_stereo_clock_and_nan_fail(self):
        for key, value in [('right',5), ('left_warp',44), ('left_receive_xr_ns',float('nan')),
                           ('right_decode_ready_xr_ns',10**16)]:
            with self.subTest(key=key):
                data = rows(); data[500][key] = value
                self.assertEqual(module.evaluate(data, self.provenance)['status'], 'fail')



class CandidateEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.receipt = {'sha256': {
            'build/codec-server/server/wivrn-pro-server': 'a'*64,
            'build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk': 'b'*64,
            'common/codec_test/vr444/direct.cpp': 'c'*64,
            'client/decoder/test_decoder.cpp': 'd'*64,
            'client/scenes/stream.cpp': 'e'*64}}
        self.helpers = {name:'e'*64 for name in ('client/scenes/stream.cpp','common/codec_test/selection_policy.h',
            'common/codec_test/selection_trace.h','server/driver/app_pacer.cpp',
            'server/driver/codec_source_trace.h','server/compositor/compositor.cpp')}
        self.receipt['sha256'].update(self.helpers)

    def seal(self, directory):
        manifest = {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in directory.rglob('*') if p.is_file() and p.name != 'hashes.json'}
        (directory/'hashes.json').write_text(json.dumps(manifest))

    def test_diagnostic_trace_cannot_be_promoted(self):
        d = self.make_run('diagnostic')
        p = d/'provenance.json'
        value = json.loads(p.read_text()); value['candidate_trace'] = True
        p.write_text(json.dumps(value)); self.seal(d)
        result = module.inspect_run(d, self.receipt)
        self.assertEqual(result['status'], 'fail')
        self.assertIn('diagnostic candidate trace is not eligible for promotion', result['errors'])

    def test_new_experimental_controls_and_hidden_source_trace_block_promotion(self):
        for key, value in (('cpu_output','mapped-direct'),('selection_policy','bounded-newest'),('source_trace',True)):
            d = self.make_run(key)
            self.mutate(d,'provenance.json',lambda p:p.update({key:value}))
            self.assertEqual(module.inspect_run(d,self.receipt)['status'],'fail')
        d = self.make_run('hidden_source')
        (d/'host').mkdir()
        (d/'host/source-trace.jsonl').write_text('{}\n')
        self.seal(d)
        self.assertIn('source tracing cannot masquerade as an untraced candidate',module.inspect_run(d,self.receipt)['errors'])

    def test_executable_helper_headers_must_match_build_receipt(self):
        for key in ('common/codec_test/selection_policy.h', 'server/driver/codec_source_trace.h'):
            for mutation in ('missing','mismatch'):
                d = self.make_run(key.replace('/','_')+mutation)
                def alter(p):
                    if mutation == 'missing':p['source_helper_sha256'].pop(key)
                    else:p['source_helper_sha256'][key]='a'*64
                self.mutate(d,'provenance.json',alter)
                self.assertIn('missing or mismatched helper source provenance',module.inspect_run(d,self.receipt)['errors'])

    def test_trace_cannot_be_hidden_by_false_provenance_flag(self):
        for key, value in (('candidate_trace_records', 1), ('render_attempts', {})):
            d = self.make_run('hidden_trace_' + key)
            p = d/'client/codec-test-present.jsonl'
            values = [json.loads(line) for line in p.read_text().splitlines()]
            values[0][key] = value
            p.write_text(''.join(json.dumps(v)+'\n' for v in values)); self.seal(d)
            result = module.inspect_run(d, self.receipt)
            self.assertEqual(result['status'], 'fail')
            self.assertIn('diagnostic trace evidence cannot masquerade as an untraced candidate',result['errors'])

    def make_run(self, name, offset=0):
        d = self.root/name
        (d/'client/codec-test').mkdir(parents=True)
        provenance = module.load_policy()['profile'] | dict(
            mode='custom-vr444', candidate_trace=False, cpu_output='cached-copy', selection_policy='closest-target', source_trace=False, server_sha256='a'*64, installed_apk_sha256='b'*64,
            headset_serial='test-headset', custom_q=8, custom_compression='lz4',
            custom_decoder='direct-neon', decoder_backend='cpu-neon', selection_phase='after-fence',
            bitrate_bps=50_000_000, scene_reference_space='Local', gaze_enabled=False,
            meta_sr_mode='off', user_presence_overridden=False, native_codec_requested=None,
            native_encoder_requested=None, native_is_hevc_carrier=False, bit_depth_requested=8,
            requested_render_node='/dev/dri/renderD129', requested_gpu_index=0,
            comparison_worker_disabled_by_test_only_guard=True,
            harness_sha256=module._sha(module.HERE/'native_compare.py'),
            direct_decoder_source_sha256='c'*64, client_decoder_source_sha256='d'*64,
            source_helper_sha256=self.helpers,
            shader_sha256={'decode.spv':'f'*64}, main_source_sha256='1'*64, runtime_manifest_sha256='2'*64)
        client = dict(preferred_refresh_rate=90, fps_divider=1, bitrate_bps=50_000_000,
                      resolution_scale=1.0, stream_scale=.3, eye_gaze=False, face_tracking=False,
                      body_tracking=False, microphone=False, hand_tracking=False,
                      openxr_post_processing={'super_sampling':0, 'sharpening':0})
        readback = {'client.json': json.dumps(client), 'codec-test-kernel.txt':'direct-neon',
                    'codec-test-backend.txt':'cpu-neon', 'codec-test-pairing.txt':'paired',
                    'codec-test-compression.txt':'lz4', 'codec-test-gaze-timing.txt':'0',
                    'codec-test-selection.txt':'after-fence','codec-test-candidate-trace.txt':'0',
                    'codec-test-cpu-output.txt':'cached-copy','codec-test-selection-policy.txt':'closest-target'}
        presented = rows(14)
        decoded = []
        for row in presented:
            for key in list(row):
                if key.endswith('_xr_ns'):
                    row[key] += offset
            t = row['submit_xr_ns']
            row.update(selection_phase='after-fence', selection_policy='closest-target', render_fence_begin_xr_ns=t-4_000_000,
                       render_fence_end_xr_ns=t-3_000_000, selection_begin_xr_ns=t-2_000_000,
                       selection_end_xr_ns=t-1_000_000, end_frame_return_xr_ns=t-100_000)
            for eye in (0,1):
                decoded.append(dict(frame=row['left'],warp=42,eye=eye,decoder_backend='cpu-neon',
                    decoder_kernel='direct-neon',cpu_vector_backend='aarch64-neon',gpu_timing_valid=False,
                    decode_gpu_ms=0,cpu_decode_ms=1,staging_copy_ms=.1,submit_fence_wait_ms=1,
                    queue_wait_ms=.1,decode_upload_ms=2,decompression_ms=.1, cpu_output_mode='cached-copy',
                    staging_memory_type=4,staging_memory_flags=15,capture_snapshot_ms=0,
                    capture_source='cpu_cached_output' if row['left']%90==0 else 'not_captured'))
        custom = dict(status='pass',decoder_selection={'status':'verified'},compression={'status':'active'},
                      runtime_validation={'status':'pass'},
                      runtime_validation_source_sha256=module._sha(module.HERE/'latency_runtime.py'))
        files = {'provenance.json':provenance,'summary.json':dict(status='pass',custom_evidence=custom,candidate_trace={'status':'off'},source_trace={'status':'off'},
                    preliminary_cpu_target_source_sha256=module._sha(module.HERE/'cpu_target.py'),
                    preliminary_cpu_target_policy_sha256=module._sha(module.HERE/'CPU-LATENCY-TARGET.json'),
                    latency_policy_source_sha256=module._sha(module.HERE/'latency_policy.py')),
                 'custom-evidence.json':custom,'client-config.json':client,'client-config-readback.json':readback,
                 'server-config.json':dict(port=9700,tcp_only=True, **{'tcp-only':True,'bit-depth':8,
                     'encoder':{'encoder':'codec-test','device':'/dev/dri/renderD129'},
                     'application':['/usr/bin/hello_xr','-s','Local']})}
        for name,value in files.items():
            (d/name).write_text(json.dumps(value))
        for name, data in [('client/codec-test-present.jsonl',presented),('client/codec-test/decode-test.jsonl',decoded)]:
            (d/name).write_text(''.join(json.dumps(row)+'\n' for row in data))
        self.seal(d)
        return d

    def mutate(self, directory, name, update):
        value = json.loads((directory/name).read_text())
        update(value)
        (directory/name).write_text(json.dumps(value))
        self.seal(directory)

    def test_valid_independent_repeats_bind_to_current_receipt(self):
        a,b = self.make_run('a'),self.make_run('b',30_000_000_000)
        report = module.evaluate_runs([a,b],self.receipt)
        self.assertEqual(report['status'],'pass',report)
        self.assertTrue(report['candidate_latency_gate_passed'])
        self.assertFalse(report['release_ready'])
        self.assertFalse(module.evaluate_runs([a],self.receipt)['candidate_latency_gate_passed'])
        self.assertEqual(module.evaluate_runs([a,b])['status'],'fail')
        self.assertEqual(module.evaluate_runs([a,b],{'sha256':{}})['status'],'fail')

    def test_same_evidence_under_different_paths_cannot_count_as_repeat(self):
        report=module.evaluate_runs([self.make_run('a'),self.make_run('b')],self.receipt)
        self.assertFalse(report['unique_runs'])
        self.assertEqual(report['status'],'fail')

    def test_native_reference_explicit_and_never_candidate(self):
        d=self.make_run('native')
        self.mutate(d,'provenance.json',lambda p:p.update(mode='native-h264'))
        self.assertEqual(module.inspect_run(d,self.receipt)['status'],'fail')
        report=module.evaluate_runs([d],reference_native=True)
        self.assertEqual(report['status'],'pass')
        self.assertTrue(report['reference_only'])
        self.assertFalse(report['candidate_latency_gate_passed'])
        self.assertFalse(report['promotion_eligible'])

    def test_missing_or_mismatched_candidate_config_and_source_rejected(self):
        for key,value in [('decoder_backend',None),('selection_phase',None),('gaze_enabled',True),
                          ('direct_decoder_source_sha256','f'*64),('source_helper_sha256',{}),('shader_sha256',{}),
                          ('meta_sr_mode','quality'),('custom_q',True),('scene_reference_space','View'),
                          ('headset_serial',''),('harness_sha256','f'*64),('server_sha256','c'*64)]:
            with self.subTest(key=key):
                d=self.make_run(key)
                self.mutate(d,'provenance.json',lambda p:p.update({key:value}))
                self.assertEqual(module.inspect_run(d,self.receipt)['status'],'fail')

    def test_readback_and_manifest_tampering_rejected(self):
        d=self.make_run('readback')
        self.mutate(d,'client-config-readback.json',lambda p:p.update({'codec-test-selection.txt':'early'}))
        self.assertEqual(module.inspect_run(d,self.receipt)['status'],'fail')
        d=self.make_run('manifest')
        (d/'client/codec-test-present.jsonl').write_text('')
        self.assertEqual(module.inspect_run(d,self.receipt)['status'],'fail')
        (d/'hashes.json').unlink()
        self.assertEqual(module.inspect_run(d,self.receipt)['status'],'fail')

    def test_raw_runtime_rechecked_even_when_summary_claims_pass(self):
        d=self.make_run('raw')
        p=d/'client/codec-test/decode-test.jsonl'
        with p.open('a') as f:
            f.write(json.dumps(dict(frame=1234,failed=True))+'\n')
        self.seal(d)
        report=module.inspect_run(d,self.receipt)
        self.assertEqual(report['status'],'fail')
        self.assertTrue(any('runtime:' in e for e in report['errors']))
        d=self.make_run('source')
        self.mutate(d,'custom-evidence.json',lambda p:p.update(runtime_validation_source_sha256='f'*64))
        self.assertEqual(module.inspect_run(d,self.receipt)['status'],'fail')

    def test_valid_but_different_effective_config_cannot_form_candidate_repeats(self):
        a,b=self.make_run('a'),self.make_run('b',30_000_000_000)
        self.mutate(b,'server-config.json',lambda p:p.update({'publish-service':'other'}))
        report=module.evaluate_runs([a,b],self.receipt)
        self.assertFalse(report['matching_candidate'])
        self.assertEqual(report['status'],'fail')


if __name__ == '__main__':
    unittest.main()
