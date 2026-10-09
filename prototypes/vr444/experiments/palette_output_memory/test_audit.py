import copy
import unittest
from audit import audit


def valid():
    rows = []
    for order, mode in enumerate(('full_copy', 'mapped_coherent', 'compact_copy')):
        row = dict(rep=0, order=order, mode=mode, input_memory_flags=6, output_memory_flags=6 if order == 1 else 1,
                   readback_memory_flags=6, stereo_copied_bytes=(1560, 0, 24)[order], queue_submissions=4 if order == 2 else 2,
                   canonical_bytes_exact=True, decoded_pixels_exact=True, malformed_pair_rejected_before_submit=True)
        row.update({k: .1 for k in ('stereo_preflight_ms', 'stereo_upload_ms', 'stereo_device_timestamp_span_ms', 'stereo_submit_wait_ms', 'stereo_assembly_crc_ms')})
        rows.append(row)
    case = dict(device='hardware', device_type=2, width=1, height=1, tiles=1, scan_blocks=1,
                stereo_uploaded_bytes=8, stereo_readback_capacity_bytes=1560, eye_wire_bytes=[39, 39], iterations=rows)
    cases = [copy.deepcopy(case) | dict(name=f'canonical-tie-{i}') for i in range(4)]
    return dict(schema=1, experiment='palette-output-memory', headset_used=False, performance_claim=False,
                promotion_blocked=True, canonical_modes=6, canonical_tie_cases=4, warmups=1, repetitions=1,
                cases=cases, case_count=4) | {'pass': True}


class AuditTests(unittest.TestCase):
    def test_scope_preserved(self):
        report = audit(valid())
        self.assertFalse(report['performance_claim'])
        self.assertTrue(report['promotion_blocked'])
        self.assertEqual(report['measured_stereo_arm_iterations'], 12)

    def test_transfer_and_fence_omissions(self):
        for mode, key, value in [(2, 'queue_submissions', 2), (2, 'stereo_copied_bytes', 16),
                                 (1, 'stereo_copied_bytes', 24), (0, 'stereo_copied_bytes', 24),
                                 (1, 'output_memory_flags', 1), (0, 'output_memory_flags', 6)]:
            data = valid(); data['cases'][0]['iterations'][mode][key] = value
            with self.subTest(mode=mode, key=key), self.assertRaises(ValueError): audit(data)

    def test_missing_arms_or_ties(self):
        data = valid(); data['cases'][0]['iterations'].pop()
        with self.assertRaises(ValueError): audit(data)
        data = valid(); data['cases'].pop(); data['case_count'] = 3
        with self.assertRaises(ValueError): audit(data)
        data = valid(); data['cases'][0]['iterations'][1]['mode'] = 'full_copy'
        with self.assertRaises(ValueError): audit(data)

    def test_false_scope_or_correctness(self):
        for key, value in [('headset_used', True), ('performance_claim', True), ('promotion_blocked', False)]:
            data = valid(); data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data)
        for key, value in [('canonical_bytes_exact', False), ('decoded_pixels_exact', False),
                           ('malformed_pair_rejected_before_submit', False), ('stereo_submit_wait_ms', float('nan'))]:
            data = valid(); data['cases'][0]['iterations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data)


if __name__ == '__main__': unittest.main()
