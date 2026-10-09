import copy
import unittest
from pathlib import Path
from audit import GOLDENS, MODES, audit, expected_cases


def fixture():
    cases, totals = [], [0, 0, 0]
    for name, (w, h, sizes) in expected_cases(False, False, []).items():
        counts = [(h+size-1)//size for size in sizes]
        modes = [[0]*count for count in counts]
        if name.startswith('golden/'):
            modes = [GOLDENS[Path(name).stem][2].copy() for _ in (0, 1)]
        for eye in modes:
            for mode in eye:
                totals[mode] += 1
        rows = [dict(mode=mode, validation_lz4_ms=.1, input_copy_ms=.1, stereo_gpu_ms=.1,
                     pipeline_wall_ms=.2, oracle_ms=.2, max_linear_error=.001,
                     max_srgb_code_error=.2, bytes_exact=True, one_eotf=True,
                     sampled_baseline_exact=True, malformed_pair_rejected_before_write_or_submit=True,
                     stereo_shape_rejected_before_write_or_submit=True,
                     upload_after_failed_admission_rejected=True) for mode in sorted(MODES)]
        cases.append(dict(name=name, width=w, height=h, eye_block_rows=sizes, eye_block_counts=counts,
                          eye_block_modes=modes, dispatch_groups=((w+127)//128)*max(counts),
                          stereo_raw_rgb_bytes=w*h*6, device='hardware', device_type=2, driver=1,
                          input_memory_flags=14, iterations=rows))
    return dict(schema=1, experiment='rowblocks-gpu', headset_used=False, promotion_blocked=True,
                format_list=False, full_fixture_matrix=False, maximum_fixtures=False, corpus_cases=0,
                cpu_prefix_restore_in_path=False, shared_prefix_barriers=False, workgroup_columns=128,
                cpu_grammar_crc_lz4_in_path=True, rrb_block_modes=3, rrb_block_mode_counts=totals,
                warmups=1, repetitions=1, cases=cases, case_count=len(cases), **{'pass': True})


class AuditTests(unittest.TestCase):
    negative_mutations = 0

    def reject(self, data):
        with self.assertRaises(ValueError):
            audit(data, {'cases': []})
        type(self).negative_mutations += 1

    @classmethod
    def tearDownClass(cls):
        print(f'negative audit mutations rejected: {cls.negative_mutations}')

    def test_exact_scope(self):
        report = audit(fixture(), {'cases': []})
        self.assertEqual(report['fixture_count'], 460)
        self.assertEqual(report['differing_stereo_block_size_fixtures'], 456)
        self.assertFalse(report['performance_gate_evaluated'])
        self.assertTrue(report['promotion_blocked'])

    def test_missing_boundaries_and_declared_scope(self):
        for key, value in [('headset_used', True), ('cpu_prefix_restore_in_path', True),
                           ('cpu_grammar_crc_lz4_in_path', False), ('shared_prefix_barriers', True),
                           ('workgroup_columns', 64), ('full_fixture_matrix', True),
                           ('maximum_fixtures', True), ('corpus_cases', 1), ('rrb_block_modes', 2),
                           ('warmups', True), ('repetitions', True)]:
            data = fixture(); data[key] = value; self.reject(data)
        data = fixture(); data['cases'].pop(); data['case_count'] -= 1; self.reject(data)
        data = fixture(); data['cases'][0]['name'] = 'same-count-invented'; self.reject(data)
        data = fixture(); data['cases'][0] = copy.deepcopy(data['cases'][1]); self.reject(data)

    def test_unequal_stereo_and_predictor_blocks(self):
        for key, value in [('eye_block_rows', [16, 16]), ('eye_block_counts', [1, 1]),
                           ('eye_block_modes', [[0], [0]]), ('dispatch_groups', 1),
                           ('stereo_raw_rgb_bytes', 6), ('height', 1)]:
            data = fixture()
            case = next(c for c in data['cases'] if c['eye_block_counts'] == [2, 1])
            case[key] = value; self.reject(data)
        data = fixture(); data['rrb_block_mode_counts'][0] += 1; self.reject(data)
        data = fixture(); data['cases'][0]['eye_block_modes'][0][0] = 3; self.reject(data)
        data = fixture(); data['cases'][0]['dispatch_groups'] = True; self.reject(data)
        data = fixture(); case = next(c for c in data['cases'] if c['name'] == 'golden/raw-up-reset.rrb')
        case['eye_block_modes'][0][1] = 0
        data['rrb_block_mode_counts'][2] -= 1; data['rrb_block_mode_counts'][0] += 1
        self.reject(data)

    def test_exactness_and_admission(self):
        for key, value in [('bytes_exact', False), ('one_eotf', False), ('sampled_baseline_exact', False),
                           ('malformed_pair_rejected_before_write_or_submit', False),
                           ('stereo_shape_rejected_before_write_or_submit', False),
                           ('upload_after_failed_admission_rejected', False),
                           ('max_srgb_code_error', .5), ('input_copy_ms', float('nan')),
                           ('oracle_ms', -1), ('mode', 'unknown')]:
            data = fixture(); data['cases'][0]['iterations'][0][key] = value; self.reject(data)
        data = fixture(); data['cases'][0]['iterations'].pop(); self.reject(data)
        data = fixture(); data['cases'][0]['iterations'][0] = copy.deepcopy(data['cases'][0]['iterations'][1]); self.reject(data)

    def test_hardware_and_memory(self):
        for key, value in [('device_type', 4), ('device_type', True), ('input_memory_flags', 2), ('input_memory_flags', -1),
                           ('driver', 2), ('device', '')]:
            data = fixture(); data['cases'][0][key] = value; self.reject(data)


if __name__ == '__main__':
    unittest.main()
