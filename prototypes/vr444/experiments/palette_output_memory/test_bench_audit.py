import copy
import unittest
from bench_audit import COMPONENTS, audit, groups


def fixture():
    source = dict(width=1, height=1, q=1, wire_bytes=39, exact=True)
    manifest = dict(cases=[source | dict(rpt='pair/frame-0.rpt'), source | dict(rpt='pair/frame-1.rpt'),
                           source | dict(rpt='synthetic/noise.rpt')])
    rows = []
    for order, mode in enumerate(('cpu_canonical', 'full_copy', 'mapped_coherent', 'compact_copy')):
        row = dict(rep=0, order=order, mode=mode, stereo_total_ms=1, stereo_pair_preflight_ms=.1,
                   canonical_bytes_exact=True, decoded_pixels_exact=True,
                   stereo_copied_bytes=(0, 1560, 0, 24)[order], queue_submissions=(0, 2, 2, 4)[order])
        row.update({k: None if order == 0 else .1 for k in COMPONENTS}); rows.append(row)
    case = dict(device='hardware', device_type=2, width=1, height=1, eye_wire_bytes=[39, 39], iterations=rows)
    data = dict(schema=1, experiment='palette-host-timing', headset_used=False, host_stage_measurement=True,
                promotion_blocked=True, cpu_reference_and_gpu_bytes_exact=True, poison_and_clear_in_total_time=True,
                warmups=3, repetitions=1, real_capture_pairs=1, synthetic_duplicated_eye_cases=1,
                source_corpus_cases=3, case_count=2,
                cases=[copy.deepcopy(case) | dict(name='corpus/pair/frame', synthetic_duplicated_eyes=False),
                       copy.deepcopy(case) | dict(name='corpus/synthetic/noise.rpt', synthetic_duplicated_eyes=True)])
    data['pass'] = True
    return data, manifest


class BenchAuditTests(unittest.TestCase):
    def test_exact_complete_scope(self):
        data, manifest = fixture(); result = audit(data, manifest)
        self.assertEqual(result['real_capture_pairs'], 1)
        self.assertEqual(result['synthetic_duplicated_eye_cases'], 1)
        self.assertTrue(result['promotion_blocked'])
        self.assertFalse(result['sub15_gate_evaluated'])

    def test_pairing_and_omitted_profiles(self):
        data, manifest = fixture(); manifest['cases'].pop(1)
        with self.assertRaises(ValueError): groups(manifest)
        data, manifest = fixture(); manifest['cases'][1]['q'] = 8
        with self.assertRaises(ValueError): groups(manifest)
        data, manifest = fixture(); data['cases'].pop(); data['case_count'] = 1
        with self.assertRaises(ValueError): audit(data, manifest)
        data, manifest = fixture(); data['cases'][1]['synthetic_duplicated_eyes'] = False
        with self.assertRaises(ValueError): audit(data, manifest)

    def test_fabricated_component_or_copy_cost(self):
        for mode, key, value in [(0, 'stereo_assembly_crc_ms', 0), (3, 'queue_submissions', 2),
                                 (3, 'stereo_copied_bytes', 16), (1, 'stereo_total_ms', float('nan')),
                                 (2, 'canonical_bytes_exact', False), (2, 'decoded_pixels_exact', False)]:
            data, manifest = fixture(); data['cases'][0]['iterations'][mode][key] = value
            with self.subTest(mode=mode, key=key), self.assertRaises(ValueError): audit(data, manifest)


if __name__ == '__main__': unittest.main()
