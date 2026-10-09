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


def output_evidence(mode='mapped-direct', frame=10, backend='cpu-neon'):
    rows = decoded(backend)
    sampled = frame % 90 == 0
    source = ('cpu_mapped_staging_snapshot' if mode == 'mapped-direct' else
              'cpu_cached_output' if mode == 'cached-copy' else
              'gpu_output_readback' if backend == 'resident' else 'decoded_cpu_output')
    for row in rows:
        row.update(frame=frame, cpu_output_mode=mode,
                   staging_memory_type=4, staging_memory_flags=15,
                   capture_source=source if sampled else 'not_captured',
                   capture_snapshot_ms=.25 if sampled and (mode == 'mapped-direct' or backend == 'resident') else 0)
        if mode == 'mapped-direct':
            row['staging_copy_ms'] = 0
    presented = presentation() | {'left': frame, 'right': frame}
    return [presented], rows


class LatencyRuntimeTests(unittest.TestCase):
    def check(self, p=None, d=None, backend='cpu-neon', phase='after-fence', status='pass', cpu_output=None):
        result = module.evaluate([presentation(phase)] if p is None else p,
                                 decoded(backend) if d is None else d, backend, phase,
                                 cpu_output=cpu_output)
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

    def test_cpu_output_valid_modes_and_actual_memory_evidence(self):
        for mode in ('cached-copy', 'mapped-direct'):
            for frame in (10, 90):
                for memory_type, flags in ((0, 6), (4, 15), (31, 14)):
                    with self.subTest(mode=mode, frame=frame, memory_type=memory_type, flags=flags):
                        p, rows = output_evidence(mode, frame)
                        for row in rows:
                            row.update(staging_memory_type=memory_type, staging_memory_flags=flags)
                        result = self.check(p=p, d=rows, cpu_output=mode)
                        self.assertEqual(result['observed_cpu_output_modes'], [mode])
        # Resident/readback still report their allocated staging memory even
        # though the CPU decoder output destination is not used.
        for backend in ('resident', 'readback'):
            for frame in (10, 90):
                p, rows = output_evidence('not_used', frame, backend)
                self.check(p=p, d=rows, backend=backend, cpu_output='not_used')

    def test_mapped_output_cannot_claim_a_staging_memcpy(self):
        p, rows = output_evidence()
        rows[0]['staging_copy_ms'] = .001
        result = self.check(p=p, d=rows, cpu_output='mapped-direct', status='fail')
        self.assertIn('mapped-direct must not report a staging memcpy', result['errors'])

    def test_output_mode_mismatch_and_mixed_modes_rejected(self):
        for requested, actual in (('mapped-direct', 'cached-copy'), ('cached-copy', 'mapped-direct')):
            p, rows = output_evidence(actual)
            result = self.check(p=p, d=rows, cpu_output=requested, status='fail')
            self.assertIn('observed CPU output differs from requested mode', result['errors'])
        p, rows = output_evidence()
        rows[1] = output_evidence('cached-copy')[1][1]
        result = self.check(p=p, d=rows, status='fail')
        self.assertIn('mixed CPU output modes within run', result['errors'])
        for backend, mode in (('resident', 'mapped-direct'), ('readback', 'cached-copy'), ('cpu-neon', 'not_used')):
            p, rows = output_evidence(mode, backend=backend)
            self.check(p=p, d=rows, backend=backend, cpu_output=mode, status='fail')

    def test_actual_staging_memory_requires_strict_type_and_coherent_visible_flags(self):
        for key, invalid in (
            ('staging_memory_type', (None, True, '4', 4.0, -1, 32, 1 << 64)),
            ('staging_memory_flags', (None, True, '15', 15.0, -1, 0, 2, 4, 8, 10, 12, 1 << 32))):
            for value in invalid:
                with self.subTest(key=key, value=value):
                    p, rows = output_evidence()
                    rows[0][key] = value
                    result = self.check(p=p, d=rows, cpu_output='mapped-direct', status='fail')
                    self.assertIn('staging memory lacks verified host-visible coherent allocation', result['errors'])

    def test_explicit_output_requires_complete_telemetry_for_each_eye(self):
        result = self.check(cpu_output='mapped-direct', status='fail')
        self.assertIn('observed CPU output differs from requested mode', result['errors'])
        keys = ('cpu_output_mode', 'staging_memory_type', 'staging_memory_flags',
                'capture_source', 'capture_snapshot_ms')
        for key in keys:
            for eye in (0, 1):
                with self.subTest(key=key, eye=eye):
                    p, rows = output_evidence(frame=90)
                    del rows[eye][key]
                    self.check(p=p, d=rows, cpu_output='mapped-direct', status='fail')
        # Historical evidence remains supported only without an explicit mode.
        self.check()

    def test_sampled_mapped_capture_source_and_cadence_are_enforced(self):
        for frame, source in ((90, 'cpu_cached_output'), (90, 'gpu_output_readback'),
                              (90, 'not_captured'), (10, 'cpu_mapped_staging_snapshot')):
            p, rows = output_evidence(frame=frame)
            rows[0]['capture_source'] = source
            result = self.check(p=p, d=rows, cpu_output='mapped-direct', status='fail')
            self.assertIn('capture source contradicts actual output path or sample cadence', result['errors'])

    def test_snapshot_clock_is_finite_nonnegative_and_inside_decode_upload(self):
        for value in (None, True, '0.25', -.001, float('nan'), float('inf'), 10**1000, 5.001):
            with self.subTest(value_type=type(value).__name__):
                p, rows = output_evidence(frame=90)
                rows[0]['capture_snapshot_ms'] = value
                self.check(p=p, d=rows, cpu_output='mapped-direct', status='fail')
        p, rows = output_evidence(frame=90)
        rows[0]['capture_snapshot_ms'] = rows[0]['decode_upload_ms']
        self.check(p=p, d=rows, cpu_output='mapped-direct')

    def test_only_actual_snapshot_paths_may_report_snapshot_overhead(self):
        for mode, frame, backend in (('mapped-direct', 10, 'cpu-neon'),
                                     ('cached-copy', 90, 'cpu-neon'),
                                     ('not_used', 90, 'readback'),
                                     ('not_used', 10, 'resident')):
            with self.subTest(mode=mode, frame=frame, backend=backend):
                p, rows = output_evidence(mode, frame, backend)
                rows[0]['capture_snapshot_ms'] = .25
                self.check(p=p, d=rows, backend=backend, cpu_output=mode, status='fail')


if __name__ == '__main__':
    unittest.main()
