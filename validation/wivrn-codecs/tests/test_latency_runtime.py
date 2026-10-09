import copy
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location('latency_runtime_test_module',
    Path(__file__).resolve().parents[1]/'integration/latency_runtime.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def presentation(phase='after-fence'):
    row = dict(left=10, right=10, left_warp=42, right_warp=42,
               left_receive_xr_ns=1_000_000, right_receive_xr_ns=2_000_000,
               left_decode_ready_xr_ns=3_000_000, right_decode_ready_xr_ns=4_000_000,
               render_fence_begin_xr_ns=5_000_000, render_fence_end_xr_ns=6_000_000,
               selection_begin_xr_ns=7_000_000, selection_end_xr_ns=8_000_000,
               end_frame_return_xr_ns=10_000_000, submit_xr_ns=11_000_000,
               selection_phase=phase)
    if phase == 'early':
        row.update(selection_begin_xr_ns=5_000_000, selection_end_xr_ns=6_000_000,
                   render_fence_begin_xr_ns=7_000_000, render_fence_end_xr_ns=8_000_000)
    return row


def decoded(backend='cpu-neon'):
    cpu = backend == 'cpu-neon'
    return [dict(frame=10, warp=42, eye=eye, decoder_backend=backend,
                 decoder_kernel='direct-neon' if cpu else 'coop128dc',
                 cpu_vector_backend='aarch64-neon' if cpu else 'not_used',
                 gpu_timing_valid=not cpu, decode_gpu_ms=0 if cpu else 2.5,
                 cpu_decode_ms=1.2 if cpu else 0, staging_copy_ms=.4 if cpu else 0,
                 submit_fence_wait_ms=2, queue_wait_ms=.5, decode_upload_ms=5,
                 decompression_ms=.02) for eye in (0, 1)]


class LatencyRuntimeTests(unittest.TestCase):
    def check(self, p=None, d=None, backend='cpu-neon', phase='after-fence', status='pass'):
        result = module.evaluate([presentation(phase)] if p is None else p,
                                 decoded(backend) if d is None else d, backend, phase)
        self.assertEqual(result['status'], status, result['errors'])
        return result

    def test_cpu_and_resident_valid_both_phases(self):
        for backend in ('cpu-neon', 'resident', 'readback'):
            for phase in ('early', 'after-fence'):
                with self.subTest(backend=backend, phase=phase):
                    result = self.check(backend=backend, phase=phase)
                    self.assertEqual(result['matched_presented_eye_identities'], 2)
                    self.assertFalse(result['optical_motion_to_photon_verified'])

    def test_native_requires_no_custom_decoder_rows(self):
        self.check(d=[], backend='native')
        self.check(d=decoded(), backend='native', status='fail')

    def test_empty_missing_and_wrong_collection_types_fail(self):
        for p, d in (([], decoded()), (None, decoded()), ({}, decoded()),
                     ([presentation()], []), ([presentation()], None)):
            self.assertEqual(module.evaluate(p, d, 'cpu-neon', 'after-fence')['status'], 'fail')
        for backend, phase in (('unknown', 'early'), ('resident', 'unknown')):
            self.assertEqual(module.evaluate([presentation()], decoded(), backend, phase)['status'], 'fail')

    def test_every_presentation_time_is_required_strict_positive_int64(self):
        for key in module._TIMES:
            for bad in (None, True, 1.5, '1', 0, -1, 1 << 63):
                with self.subTest(key=key, bad=bad):
                    self.check(p=[presentation() | {key: bad}], status='fail')

    def test_phase_and_every_order_boundary(self):
        self.check(p=[presentation('early')], status='fail')
        for phase in ('early', 'after-fence'):
            names = (['selection_begin_xr_ns', 'selection_end_xr_ns',
                      'render_fence_begin_xr_ns', 'render_fence_end_xr_ns'] if phase == 'early'
                     else ['render_fence_begin_xr_ns', 'render_fence_end_xr_ns',
                           'selection_begin_xr_ns', 'selection_end_xr_ns'])
            names += ['end_frame_return_xr_ns', 'submit_xr_ns']
            for earlier, later in zip(names, names[1:]):
                row = presentation(phase)
                row[earlier] = row[later] + 1
                self.check(p=[row], phase=phase, status='fail')

    def test_ready_during_selection_allowed_after_selection_rejected(self):
        row = presentation()
        row['right_decode_ready_xr_ns'] = row['selection_end_xr_ns']
        self.check(p=[row])
        row['right_decode_ready_xr_ns'] += 1
        self.check(p=[row], status='fail')
        self.check(p=[presentation() | {'left_receive_xr_ns': 4_000_000}], status='fail')

    def test_exact_stereo_and_decode_identity_join(self):
        for change in ({'right': 11}, {'right_warp': 43}, {'left': True}):
            self.check(p=[presentation() | change], status='fail')
        for change in ({'frame': 11}, {'warp': 43}, {'eye': True}):
            rows = decoded()
            rows[1].update(change)
            self.check(d=rows, status='fail')

    def test_repeats_stable_and_submit_strictly_increasing(self):
        row = presentation()
        repeat = row | {key: row[key]+20_000_000 for key in (
            'selection_begin_xr_ns', 'selection_end_xr_ns',
            'render_fence_begin_xr_ns', 'render_fence_end_xr_ns',
            'end_frame_return_xr_ns', 'submit_xr_ns')}
        self.check(p=[row, repeat])
        self.check(p=[row, row], status='fail')
        self.check(p=[row, row | {'submit_xr_ns': 12_000_000}], status='fail')
        self.check(p=[row, repeat | {'right_decode_ready_xr_ns': 4_000_001}], status='fail')

    def test_decoder_durations_finite_nonnegative_real_numbers(self):
        for key in module._DURATIONS:
            for bad in (None, True, '0', -1, float('nan'), float('inf'), 10**1000):
                rows = decoded()
                rows[0][key] = bad
                with self.subTest(key=key, bad_type=type(bad).__name__):
                    self.check(d=rows, status='fail')

    def test_cpu_provenance_flags_no_gpu_duration_claim(self):
        for change in ({'gpu_timing_valid': True}, {'gpu_timing_valid': 0},
                       {'decode_gpu_ms': .01}, {'cpu_vector_backend': 'scalar'},
                       {'decoder_kernel': 'coop128dc'}, {'decoder_backend': 'resident'}):
            rows = decoded()
            rows[0].update(change)
            self.check(d=rows, status='fail')

    def test_resident_provenance_flags_and_no_cpu_staging_claim(self):
        for change in ({'gpu_timing_valid': False}, {'gpu_timing_valid': 1},
                       {'cpu_decode_ms': .1}, {'staging_copy_ms': .1},
                       {'cpu_vector_backend': 'aarch64-neon'}, {'decoder_kernel': 'direct-neon'}):
            rows = decoded('resident')
            rows[0].update(change)
            self.check(d=rows, backend='resident', status='fail')

    def test_failed_duplicate_and_mixed_kernel_rejected(self):
        self.check(d=decoded()+[dict(frame=11, failed=True)], status='fail')
        self.check(d=decoded()+[decoded()[0]], status='fail')
        rows = decoded('resident')
        rows[1]['decoder_kernel'] = 'coop128'
        self.check(d=rows, backend='resident', status='fail')

    def test_mapping_supported_with_key_identity_check(self):
        rows = decoded()
        self.check(d={(r['frame'], r['eye']): r for r in rows})
        self.check(d={(10, 0): rows[0], (11, 1): rows[1]}, status='fail')

    def test_observational_distributions_and_no_input_mutation(self):
        p, d = [presentation()], decoded()
        before = copy.deepcopy((p, d))
        result = self.check(p=p, d=d)
        self.assertEqual(result['render_fence_wait_ms']['p50'], 1)
        self.assertEqual(result['selection_end_to_end_frame_return_ms']['p50'], 2)
        self.assertEqual((p, d), before)


if __name__ == '__main__':
    unittest.main()
