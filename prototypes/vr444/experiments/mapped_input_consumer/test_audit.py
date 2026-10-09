import copy
import importlib.util
import unittest
from pathlib import Path
from audit import ARMS, TIMES, audit, matched_variants

_spec = importlib.util.spec_from_file_location('mapped_fixture', Path(__file__).resolve().parents[1] / 'mapped_input_decode/test_audit.py')
fixture_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(fixture_module)


def fixture():
    data, manifest, blocks = fixture_module.fixture()
    data['experiment'] = 'mapped-input-consumer'
    for row in data['cases'][0]['iterations']:
        row.update(first_consumer_wall_ms=2., first_consumer_device_ms=1., joint_outer_wall_ms=8.1,
                   first_consumer_submissions=1, joint_queue_submissions=3,
                   first_consumer_saved_before_oracle=True, first_consumer_samples_exact=True,
                   right_consumer_omission_rejected=True)
    return data, manifest, blocks


class ConsumerAuditTests(unittest.TestCase):
    def test_valid_joined_clock(self):
        data, manifest, blocks = fixture()
        report = audit(data, manifest, blocks)
        self.assertEqual(report['right_eye_omission_controls'], 9)
        self.assertEqual(report['per_case'][0]['arms'][ARMS[0]]['joint_outer_wall']['p50_ms'], 8.1)
        self.assertEqual(report['per_case'][0]['arms'][ARMS[0]]['outer_wall']['p50_ms'], 6.)

    def test_missing_first_consumer_cannot_be_repaired_by_oracle(self):
        for key in ('first_consumer_saved_before_oracle', 'first_consumer_samples_exact',
                    'right_consumer_omission_rejected'):
            for value in (False, 1, None):
                data, manifest, blocks = fixture(); data['cases'][0]['iterations'][0][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    audit(data, manifest, blocks)

    def test_clock_and_submission_rejections(self):
        changes = [('joint_outer_wall_ms', 7.9), ('first_consumer_wall_ms', .5),
                   ('first_consumer_device_ms', -1), ('first_consumer_wall_ms', float('nan')),
                   ('joint_outer_wall_ms', float('inf')), ('first_consumer_device_ms', True),
                   ('first_consumer_submissions', 0), ('first_consumer_submissions', True),
                   ('joint_queue_submissions', 2), ('joint_queue_submissions', 3.)]
        for key, value in changes:
            data, manifest, blocks = fixture(); data['cases'][0]['iterations'][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                audit(data, manifest, blocks)

    def test_base_readiness_checks_are_retained(self):
        for key, value in [('input_memory_flags', [0, 0]), ('intermediate_pixel_copy_bytes', 0),
                           ('timed_queue_submissions', 1), ('image_bytes_exact', False),
                           ('malformed_pair_rejected_before_write_or_submit', False)]:
            data, manifest, blocks = fixture(); data['cases'][0]['iterations'][0][key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                audit(data, manifest, blocks)
        data, manifest, blocks = fixture(); data['experiment'] = 'mapped-input-decode'
        with self.assertRaises(ValueError): audit(data, manifest, blocks)


if __name__ == '__main__':
    unittest.main()
