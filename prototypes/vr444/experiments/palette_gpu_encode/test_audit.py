import copy
import unittest
from audit import audit


def valid():
    row = dict(rep=0, canonical_bytes_exact=True, decoded_pixels_exact=True, malformed_pair_rejected_before_submit=True)
    row.update({k: .1 for k in ('stereo_preflight_ms', 'stereo_upload_ms', 'stereo_gpu_and_full_readback_ms', 'stereo_submit_wait_ms', 'stereo_assembly_crc_ms')})
    case = dict(name='reference', device='hardware', device_type=2, width=16, height=16, tiles=1, scan_blocks=1,
                stereo_uploaded_bytes=2048, stereo_readback_capacity_bytes=1560, stereo_wire_bytes=78,
                input_memory_flags=6, output_memory_flags=1, readback_memory_flags=6, iterations=[row])
    return dict(schema=1, experiment='palette-gpu-encode', headset_used=False, performance_claim=False,
                promotion_blocked=True, canonical_modes=6, warmups=1, repetitions=1, cases=[case], case_count=1) | {'pass': True}


class AuditTests(unittest.TestCase):
    def test_limits_preserved(self):
        result = audit(valid())
        self.assertFalse(result['performance_claim'])
        self.assertFalse(result['headset_used'])
        self.assertTrue(result['promotion_blocked'])

    def test_scope_and_metadata_mutations(self):
        mutations = [('performance_claim', True), ('headset_used', True), ('promotion_blocked', False),
                     ('case_count', 2), ('warmups', 0), ('canonical_modes', 5)]
        for key, value in mutations:
            data = valid(); data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data)
        for key, value in [('device_type', 4), ('tiles', 2), ('scan_blocks', 0), ('width', 4097),
                           ('stereo_readback_capacity_bytes', 78), ('stereo_uploaded_bytes', 0),
                           ('stereo_wire_bytes', 99999), ('output_memory_flags', 0)]:
            data = valid(); data['cases'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data)

    def test_correctness_and_timing_mutations(self):
        for key, value in [('canonical_bytes_exact', False), ('decoded_pixels_exact', False),
                           ('malformed_pair_rejected_before_submit', False), ('rep', 1),
                           ('stereo_assembly_crc_ms', float('nan')), ('stereo_preflight_ms', -.1)]:
            data = valid(); data['cases'][0]['iterations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data)
        data = valid(); data['cases'].append(copy.deepcopy(data['cases'][0])); data['case_count'] = 2
        with self.assertRaises(ValueError): audit(data)


if __name__ == '__main__': unittest.main()
