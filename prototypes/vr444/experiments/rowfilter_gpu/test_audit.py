import copy
import unittest
from audit import MODES, TIMES, audit


def fixture():
    row = dict(bytes_exact=True, one_eotf=True, sampled_baseline_exact=True,
               malformed_pair_rejected_before_write_or_submit=True,
               max_linear_error=.002, max_srgb_code_error=.2)
    row.update({key: .1 for key in TIMES})
    cases = [dict(name=str(i), width=17, height=129, device='hardware', device_type=2,
                  driver=1, input_memory_flags=14, dispatch_groups=5, stereo_raw_rgb_bytes=17*129*6,
                  iterations=[dict(row, mode=mode) for mode in sorted(MODES)]) for i in range(3)]
    return dict(schema=1, experiment='rowfilter-gpu', columns=4, format_list=False, warmups=1,
                repetitions=1, headset_used=False, cpu_prefix_restore_in_path=False,
                cpu_grammar_crc_lz4_in_path=True, rrf_modes=3, rrf_eye_mode_counts=[2, 2, 2],
                case_count=3, cases=cases, promotion_blocked=True, **{'pass': True})


class AuditTests(unittest.TestCase):
    def test_preserves_scope(self):
        report = audit(fixture())
        self.assertTrue(report['pass_'])
        self.assertFalse(report['performance_gate_evaluated'])
        self.assertTrue(report['promotion_blocked'])

    def test_missing_or_forged_gate(self):
        for key, value in [('columns', 2), ('columns', True), ('cpu_prefix_restore_in_path', True),
                           ('cpu_grammar_crc_lz4_in_path', False), ('rrf_eye_mode_counts', [0, 2, 4]),
                           ('rrf_eye_mode_counts', [1, 1, 1]), ('promotion_blocked', False)]:
            data = fixture(); data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(data)

    def test_shape_device_and_upload(self):
        for key, value in [('width', 4097), ('height', 0), ('dispatch_groups', 4),
                           ('stereo_raw_rgb_bytes', 0), ('input_memory_flags', 2),
                           ('device_type', 4), ('driver', 3)]:
            data = fixture(); data['cases'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(data)

    def test_exact_samples_and_arms(self):
        for key, value in [('bytes_exact', False), ('sampled_baseline_exact', False),
                           ('malformed_pair_rejected_before_write_or_submit', False),
                           ('one_eotf', False), ('max_srgb_code_error', .5),
                           ('stereo_gpu_ms', float('nan')), ('max_linear_error', -1)]:
            data = fixture(); data['cases'][0]['iterations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(data)
        data = fixture(); data['cases'][0]['iterations'].pop()
        with self.assertRaises(ValueError): audit(data)
        data = fixture(); data['cases'][1] = copy.deepcopy(data['cases'][0])
        with self.assertRaises(ValueError): audit(data)


if __name__ == '__main__':
    unittest.main()
