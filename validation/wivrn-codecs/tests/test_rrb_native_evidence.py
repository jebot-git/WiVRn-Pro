import copy
import importlib.util
from pathlib import Path
import unittest

from test_latency_runtime import presentation, decoded

HERE = Path(__file__).parents[1]/'integration'
def module(name):
    spec = importlib.util.spec_from_file_location('rrb_test_'+name, HERE/(name+'.py'))
    result = importlib.util.module_from_spec(spec); spec.loader.exec_module(result); return result
runtime, live = module('latency_runtime'), module('live')


def fixture(output='cpu', frame=10):
    rows = decoded()
    for r in rows:
        r.update(frame=frame, effective_codec='rrb32', rrb_output=output,
                 configured_rrb_workers=4, rrb_workers=4 if output == 'cpu' else 0,
                 rrb_generation=7, rrb_vector_backend='aarch64-neon' if output == 'cpu' else 'not_used_residual_unpack',
                 rrb_block_rows=32, stereo_envelope_cap=262144, pair_decode_ms=1.5,
                 pair_decode_scope='stereo_once_reported_per_eye',
                 cpu_decode_ms=0, cpu_vector_backend='not_used' if output == 'cpu' else 'not_used_residual_unpack',
                 cpu_output_mode='not_used', capture_snapshot_ms=.25 if output == 'staged-gpu' and frame % 90 == 0 else 0,
                 capture_source=('rrb_pair_cpu_output' if output == 'cpu' else 'rrb_gpu_image_readback') if frame % 90 == 0 else 'not_captured')
        if output == 'cpu': r.update(staging_memory_type=4, staging_memory_flags=15)
        else: r.update(staging_prepare_ms=.2, staging_copy_ms=0, staging_copy_timing_valid=False)
    p = presentation() | {'left': frame, 'right': frame}
    return [p], rows


class Runtime(unittest.TestCase):
    def evaluate(self, output, frame=10, changes=None):
        p, d = fixture(output, frame)
        if changes: d[1].update(changes)
        return runtime.evaluate(p, d, 'cpu-neon', 'after-fence', cpu_output='not_used', rrb_output=output, rrb_workers=4)

    def test_explicit_cpu_and_gpu_contracts(self):
        for output in ('cpu', 'staged-gpu'):
            for frame in (10, 90):
                r = self.evaluate(output, frame)
                self.assertEqual(r['status'], 'pass', r['errors'])

    def test_mislabeled_workers_and_direct_decode_rejected(self):
        for output, changes in (('cpu', {'rrb_vector_backend': 'scalar'}),
                                ('cpu', {'cpu_decode_ms': 1.5}),
                                ('cpu', {'rrb_workers': 2}),
                                ('staged-gpu', {'rrb_workers': 4}),
                                ('staged-gpu', {'gpu_timing_valid': True}),
                                ('staged-gpu', {'staging_prepare_ms': float('nan')}),
                                ('staged-gpu', {'staging_copy_ms': 9}),
                                ('staged-gpu', {'staging_copy_timing_valid': True}),
                                ('staged-gpu', {'rrb_vector_backend': 'aarch64-neon'}),
                                ('staged-gpu', {'cpu_vector_backend': 'aarch64-neon'})):
            self.assertEqual(self.evaluate(output, changes=changes)['status'], 'fail')

    def test_sample_boundary_is_not_interchangeable(self):
        for output in ('cpu', 'staged-gpu'):
            self.assertEqual(self.evaluate(output, 90, {'capture_source': 'cpu_cached_output'})['status'], 'fail')
            self.assertEqual(self.evaluate(output, 10, {'capture_snapshot_ms': .2})['status'], 'fail')

    def test_cannot_pass_legacy_direct_contract(self):
        p, d = fixture()
        self.assertEqual(runtime.evaluate(p, d, 'cpu-neon', 'after-fence', cpu_output='cached-copy')['status'], 'fail')


class Admission(unittest.TestCase):
    def setUp(self):
        self.encoded = {(10, e): dict(effective_codec='rrb32', source_contract='post_foveation_rgb_exact',
            rrb_block_rows=32, stereo_envelope_cap=262144, quantizer=1, payload_bytes=n)
            for e, n in ((0, 200000), (1, 62144))}
        self.decoded = {(r['frame'], r['eye']): r for r in fixture()[1]}
        self.provenance = dict(rrb_workers=4, rrb_output='cpu')

    def evaluate(self, rejected=()):
        return live.rrb_metrics(self.encoded, self.decoded, rejected, 'rrb32-q1', self.provenance)

    def test_asymmetric_exact_cap_and_pair_duration_count_once(self):
        r = self.evaluate()
        self.assertEqual(r['status'], 'verified', r['errors'])
        self.assertEqual(r['shared_pair_decode_ms']['count'], 1)

    def test_cap_plus_one_and_partial_source_fail(self):
        self.encoded[10, 1]['payload_bytes'] += 1
        self.assertEqual(self.evaluate()['status'], 'mismatch')
        self.encoded.pop((10, 1))
        self.assertEqual(self.evaluate()['status'], 'mismatch')

    def test_source_quantization_and_pair_timing_differ(self):
        self.encoded[10, 0]['quantizer'] = 8
        self.assertEqual(self.evaluate()['status'], 'mismatch')
        self.encoded[10, 0]['quantizer'] = 1
        self.decoded[10, 1]['pair_decode_ms'] = 1.6
        self.assertEqual(self.evaluate()['status'], 'mismatch')

    def test_rejected_pair_cannot_be_decoded(self):
        rejected = [dict(frame=10, eye=0, pair_cap=262144, staged_pair_bytes=0, reason='batch_failed_or_unsubmitted')]
        self.assertEqual(self.evaluate(rejected)['status'], 'mismatch')
        rejected[0]['frame'] = 11
        self.assertEqual(self.evaluate(rejected)['status'], 'verified')
        rejected[0]['reason'] = 'cap_exceeded'
        self.assertEqual(self.evaluate(rejected)['status'], 'mismatch')

    def test_peer_generation_scope_and_source_quantizer_are_bound(self):
        for changes in ({'rrb_generation': 8}, {'pair_decode_scope': 'per_eye_independent'}):
            original = self.decoded[10, 1].copy()
            self.decoded[10, 1].update(changes)
            self.assertEqual(self.evaluate()['status'], 'mismatch')
            self.decoded[10, 1] = original
        for row in self.encoded.values(): row.update(source_contract='decoded_vr444_rgb_exact', quantizer=8)
        self.provenance['custom_q'] = 8
        self.assertEqual(live.rrb_metrics(self.encoded, self.decoded, (), 'rrb32-vr444', self.provenance)['status'], 'verified')
        self.provenance['custom_q'] = 1
        self.assertEqual(live.rrb_metrics(self.encoded, self.decoded, (), 'rrb32-vr444', self.provenance)['status'], 'mismatch')


if __name__ == '__main__': unittest.main()
