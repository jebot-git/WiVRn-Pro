import copy
import unittest
from audit import ARMS, audit, groups, qualification_shapes


def fixture():
    source = dict(width=1, height=1, q=1, wire_bytes=35, exact=True)
    manifest = dict(cases=[source | dict(rrf='pair/frame-0.rrf', source_kind='captured_eye'),
                           source | dict(rrf='pair/frame-1.rrf', source_kind='captured_eye'),
                           source | dict(rrf='synthetic/a.rrf', source_kind='synthetic')])
    cases = []
    for i, name in enumerate(('corpus/pair/frame', 'corpus/synthetic/a.rrf')):
        rows = []
        for order in range(2):
            mode = ARMS[(i+3+order)%2]
            rows.append(dict(rep=0, order=order, mode=mode, stereo_total_ms=1, stereo_poison_ms=.2,
                             stereo_preflight_decode_ms=.2, stereo_upload_ms=.1, stereo_pipeline_wall_ms=.4,
                             stereo_device_span_ms=.3, max_srgb_code_error=.2,
                             stereo_ready_and_sampled_ms=1.3, stereo_consumer_wall_ms=.25,
                             stereo_consumer_device_ms=.1, consumer_queue_submissions=1,
                             consumer_sampled_baseline_exact=True,
                             stereo_uploaded_pixel_bytes=8, timed_queue_submissions=2,
                             image_bytes_exact=True, sampled_cpu_copy_baseline_exact=True,
                             malformed_pair_rejected_before_write_or_submit=True))
        cases.append(dict(name=name, synthetic_duplicated_eyes=bool(i), device='hardware', device_type=2,
                          driver=1, width=1, height=1, eye_wire_bytes=[35, 35], iterations=rows,
                          host_availability_and_shape_rejections=True,
                          staging_image_memory=[dict(type=0, flags=1, type_bits=1, allocation_bytes=4096)]*2,
                          host_image_memory=[dict(type=1, flags=15, type_bits=2, allocation_bytes=4096)]*2,
                          host_copy_capability=dict(queried=True, dependencies_enabled=True,
                              feature_enabled=True, format_supported=True, combined_usage_supported=True,
                              usage=0x00400007, source_layouts=[1,7], destination_layouts=[1,5],
                              optimal_device_access=False, identical_memory_layout=False,
                              identical_memory_type_requirements=False)))
    return dict(schema=1, experiment='host-image-consumer-timing', qualification_only=False, headset_used=False,
                promotion_blocked=True, warmups=3, repetitions=1, source_corpus_cases=3,
                real_capture_pairs=1, synthetic_duplicated_eye_cases=1,
                cpu_backend='x86-ssse3-pack-expand+sse2-row', checked_crc_grammar_lz4_in_every_arm=True,
                poison_in_total_time=True, oracle_outside_total_time=True, reused_workspaces=True,
                case_count=2, cases=cases, **{'pass':True}), manifest


class AuditTests(unittest.TestCase):
    def test_qualification_boundaries_not_just_counts(self):
        data, manifest = fixture(); template = data['cases'][0]
        data.update(qualification_only=True, warmups=1, source_corpus_cases=0,
                    real_capture_pairs=0, synthetic_duplicated_eye_cases=0)
        data['cases'] = []
        for i, (name, (w, h)) in enumerate(qualification_shapes(manifest).items()):
            case = copy.deepcopy(template); case.update(name=name, width=w, height=h)
            for order, row in enumerate(case['iterations']):
                mode = ARMS[(i+1+order)%2]
                row.update(mode=mode, stereo_uploaded_pixel_bytes=w*h*8)
            data['cases'].append(case)
        data['case_count'] = len(data['cases'])
        self.assertEqual(audit(data, manifest)['case_count'], 135)
        bad = copy.deepcopy(data); bad['cases'][0]['name'] = 'invented/same-count'
        with self.assertRaises(ValueError): audit(bad, manifest)
        bad = copy.deepcopy(data); bad['cases'][0]['height'] += 1
        with self.assertRaises(ValueError): audit(bad, manifest)

    def test_exact_accounting_and_scope(self):
        data, manifest = fixture(); report = audit(data, manifest)
        self.assertEqual(report['measured_stereo_arm_invocations'], 4)
        self.assertFalse(report['sub15_gate_evaluated'])
        self.assertTrue(report['promotion_blocked'])

    def test_pair_identity_and_origin(self):
        data, manifest = fixture(); manifest['cases'].pop(1)
        with self.assertRaises(ValueError): groups(manifest)
        data, manifest = fixture(); manifest['cases'][1]['q'] = 8
        with self.assertRaises(ValueError): groups(manifest)
        data, manifest = fixture(); manifest['cases'][2]['source_kind'] = 'captured_eye'
        with self.assertRaises(ValueError): groups(manifest)
        data, manifest = fixture(); data['cases'][1]['synthetic_duplicated_eyes'] = False
        with self.assertRaises(ValueError): audit(data, manifest)

    def test_forged_scope_and_omissions(self):
        for key, value in [('poison_in_total_time', False), ('oracle_outside_total_time', False),
                           ('reused_workspaces', False), ('checked_crc_grammar_lz4_in_every_arm', False),
                           ('cpu_backend', 'unknown'), ('qualification_only', True)]:
            data, manifest = fixture(); data[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data, manifest)
        data, manifest = fixture(); data['cases'].pop(); data['case_count'] = 1
        with self.assertRaises(ValueError): audit(data, manifest)

    def test_missing_cost_or_exactness(self):
        for key, value in [('stereo_total_ms', .5), ('stereo_poison_ms', 2),
                           ('stereo_uploaded_pixel_bytes', 4), ('timed_queue_submissions', 1),
                           ('image_bytes_exact', False), ('sampled_cpu_copy_baseline_exact', False),
                           ('malformed_pair_rejected_before_write_or_submit', False),
                           ('max_srgb_code_error', .5), ('stereo_device_span_ms', float('nan')),
                           ('order', 4)]:
            data, manifest = fixture(); data['cases'][0]['iterations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data, manifest)
        data, manifest = fixture(); data['cases'][0]['iterations'].pop()
        with self.assertRaises(ValueError): audit(data, manifest)
        data, manifest = fixture(); data['cases'][0]['iterations'][0] = copy.deepcopy(data['cases'][0]['iterations'][1])
        with self.assertRaises(ValueError): audit(data, manifest)


    def test_missing_capability_evidence(self):
        mutations = [(key, False) for key in ('queried', 'dependencies_enabled', 'feature_enabled',
                    'format_supported', 'combined_usage_supported')]
        mutations += [('usage', 7), ('source_layouts', [1]), ('source_layouts', [7]),
                      ('destination_layouts', [1]), ('destination_layouts', [5]),
                      ('optimal_device_access', None), ('identical_memory_layout', None),
                      ('identical_memory_type_requirements', None)]
        for key, value in mutations:
            data, manifest = fixture();data['cases'][0]['host_copy_capability'][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data, manifest)
        data, manifest = fixture();data['cases'][0]['host_availability_and_shape_rejections'] = False
        with self.assertRaises(ValueError): audit(data, manifest)


    def test_consumer_cost_and_output_not_hidden(self):
        for key, value in [('stereo_ready_and_sampled_ms', 1), ('stereo_consumer_wall_ms', -1),
                           ('stereo_consumer_device_ms', float('inf')), ('consumer_queue_submissions', 0),
                           ('consumer_sampled_baseline_exact', False)]:
            data, manifest = fixture();data['cases'][0]['iterations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data, manifest)

    def test_actual_allocation_evidence(self):
        for key, value in [('type', 32), ('type_bits', 0), ('allocation_bytes', 0), ('flags', 14),
                           ('flags', -1), ('type_bits', -1), ('allocation_bytes', 1 << 64)]:
            data, manifest = fixture();data['cases'][0]['host_image_memory'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError): audit(data, manifest)
        data, manifest = fixture();data['cases'][0]['staging_image_memory'].pop()
        with self.assertRaises(ValueError): audit(data, manifest)


if __name__ == '__main__': unittest.main()
