import json
import tempfile
import unittest
from pathlib import Path
from run import require_cpu_matrix, require_python_tests, required_stages


class RunnerGateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'log'

    def test_zero_or_failed_python_is_not_a_pass(self):
        for text, status in (('Ran 0 tests in 0.0s\n\nOK\n', 'pass'),
                             ('Ran 5 tests in 0.0s\n\nFAILED\n', 'fail'),
                             ('Ran 5 tests in 0.0s\n\nFAILED\n', 'pass'),
                             ('Ran 5 tests in 0.0s\n\nOK (skipped=5)\n', 'pass'),
                             ('', 'pass')):
            self.path.write_text(text)
            with self.subTest(text=text), self.assertRaises(ValueError):
                require_python_tests({'stderr': str(self.path), 'status': status})
        self.path.write_text('Ran 5 tests in 0.0s\n\nOK\n')
        result = {'stderr': str(self.path), 'status': 'pass'}
        require_python_tests(result)
        self.assertEqual(result['test_count'], 5)

    def test_cpu_scope_is_complete_and_no_vulkan(self):
        value = {'pass': True, 'cases': 838, 'reuse_rounds': 2, 'rejections': 8382,
                 'late_partial_decode_withheld': True, 'vulkan_initialized': False,
                 'headset_used': False}
        self.path.write_text(json.dumps(value))
        require_cpu_matrix({'stdout': str(self.path), 'status': 'pass'})
        for key, replacement in (('cases', 837), ('reuse_rounds', 1), ('rejections', 0),
                                 ('late_partial_decode_withheld', False),
                                 ('vulkan_initialized', True), ('headset_used', True),
                                 ('headset_used', 0), ('pass', 1), ('cases', 838.0)):
            changed = dict(value, **{key: replacement})
            self.path.write_text(json.dumps(changed))
            with self.subTest(key=key), self.assertRaises(ValueError):
                require_cpu_matrix({'stdout': str(self.path), 'status': 'pass'})

    def test_levels_cannot_hide_missing_host_device(self):
        for devices in ([], [0, 0], [-1]):
            with self.subTest(devices=devices), self.assertRaises(ValueError):
                required_stages('host', devices)
        audits = required_stages('audits', [])
        cpu = required_stages('cpu', [])
        host = required_stages('host', [0, 1])
        self.assertEqual(cpu[:len(audits)], audits)
        self.assertEqual(host[:len(cpu)], cpu)
        self.assertEqual(host[-4:], ['host-device-0', 'host-audit-0', 'host-device-1', 'host-audit-1'])
        with self.assertRaises(ValueError):
            required_stages('invented', [])


if __name__ == '__main__':
    unittest.main()
