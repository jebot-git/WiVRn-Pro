import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location('research_suite', Path(__file__).parents[1] / 'research_suite.py')
suite = importlib.util.module_from_spec(spec)
spec.loader.exec_module(suite)


class OfflineExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'source'
        self.root.mkdir()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        self.tests = self.root / 'prototypes/vr444'
        self.tests.mkdir(parents=True)
        self.out = self.base / 'output'

    def fixture(self, body):
        (self.tests / 'test_fixture.py').write_text('import unittest\nfrom pathlib import Path\n'
                                                   'class Check(unittest.TestCase):\n'
                                                   '    def test_check(self):\n' + body + '\n')

    def invoke(self):
        return suite.run(self.root, self.out, ['codec-python'])

    def test_passing_receipt_binds_actual_source_and_outputs(self):
        self.fixture('        self.assertEqual(2 + 3, 5)')
        result = self.invoke()
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['python_tests'], 1)
        self.assertFalse(result['headset_operations'])
        self.assertFalse(result['promotion_eligible'])
        manifest = json.loads((self.out / 'source-manifest.json').read_text())
        self.assertEqual(manifest['prototypes/vr444/test_fixture.py'],
                         suite.digest(self.tests / 'test_fixture.py'))
        hashes = json.loads((self.out / 'hashes.json').read_text())
        self.assertEqual(hashes['summary.json'], suite.digest(self.out / 'summary.json'))

    def test_empty_discovery_is_not_success(self):
        self.assertEqual(self.invoke()['status'], 'fail')
        result = json.loads((self.out / 'summary.json').read_text())
        self.assertEqual(result['tasks'][0]['error'], 'no Python tests collected')

    def test_failing_assertion_stays_failed(self):
        self.fixture('        self.fail("deliberate oracle mismatch")')
        result = self.invoke()
        self.assertEqual(result['status'], 'fail')
        self.assertNotEqual(result['tasks'][0]['commands'][0]['exit_code'], 0)

    def test_source_edit_during_successful_execution_invalidates_receipt(self):
        self.fixture('        Path(__file__).write_text("# changed source\\n")')
        result = self.invoke()
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['source_changed'], ['prototypes/vr444/test_fixture.py'])

    def test_existing_evidence_directory_is_never_reused(self):
        self.out.mkdir()
        marker = self.out / 'untouched'
        marker.write_text('keep')
        with self.assertRaises(FileExistsError):
            self.invoke()
        self.assertEqual(marker.read_text(), 'keep')
        self.assertEqual(list(self.out.iterdir()), [marker])

    def test_unknown_or_empty_task_selection_is_rejected_before_writes(self):
        for selected in ([], ['headset-launch']):
            with self.assertRaises(ValueError):
                suite.run(self.root, self.out, selected)
        self.assertFalse(self.out.exists())

    def test_missing_compiler_is_recorded_as_failure(self):
        result = suite.run(self.root, self.out, ['wire-bounds'], str(self.base / 'no-compiler'))
        self.assertEqual(result['status'], 'fail')
        self.assertEqual(result['tasks'][0]['commands'][0]['status'], 'unavailable')

    def test_timeout_cannot_become_a_pass(self):
        result = suite.execute([sys.executable, '-c', 'import time; time.sleep(30)'],
                               self.root, self.base / 'timeout.log', .1, os.environ.copy())
        self.assertEqual(result['status'], 'timeout')
        self.assertNotEqual(result['exit_code'], 0)
        self.assertLess(result['elapsed_s'], 5)


if __name__ == '__main__':
    unittest.main()
