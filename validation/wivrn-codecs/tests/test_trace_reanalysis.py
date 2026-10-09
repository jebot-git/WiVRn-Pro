import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_candidate_trace import bounded_fixture

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / 'prototypes/vr444/experiments/selection_policy/reanalyze_trace.py'
spec = importlib.util.spec_from_file_location('trace_reanalysis', SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class TraceReanalysisTests(unittest.TestCase):
    def frozen_run(self, directory):
        run = Path(directory) / 'original'
        run.mkdir()
        trace, present = bounded_fixture()
        values = {
            'summary.json': {'status': 'fail', 'candidate_trace': {'status': 'fail', 'errors': ['historical']}},
            'provenance.json': {'candidate_trace': True, 'selection_policy': 'bounded-newest'},
            'cleanup.json': {'restored': True, 'cleanup_errors': []},
            'client/codec-test/selection-trace.jsonl': trace,
            'client/codec-test-present.jsonl': present,
        }
        for name, value in values.items():
            path = run / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value) + '\n')
        self.manifest(run)
        return run

    def manifest(self, run):
        value = {str(p.relative_to(run)): module.sha(p) for p in run.rglob('*')
                 if p.is_file() and p.name != 'hashes.json'}
        (run / 'hashes.json').write_text(json.dumps(value))

    def test_reanalysis_preserves_failed_original_and_disclaims_qualification(self):
        with tempfile.TemporaryDirectory() as folder:
            run = self.frozen_run(folder)
            before = {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()}
            result = module.analyze(run)
            self.assertEqual(result['status'], 'pass')
            self.assertEqual(result['original_run_status'], 'fail')
            self.assertEqual(result['original_manifest_files_verified'], 5)
            for key in ('original_result_overwritten', 'qualification_changed', 'promotion_eligible', 'release_ready'):
                self.assertIs(result[key], False)
            self.assertEqual(before, {str(p): p.read_bytes() for p in run.rglob('*') if p.is_file()})

    def test_tampered_or_missing_original_evidence_fails_before_reanalysis(self):
        for name in ('summary.json', 'client/codec-test/selection-trace.jsonl'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                run = self.frozen_run(folder)
                (run / name).write_text('{}\n')
                with self.assertRaisesRegex(ValueError, 'manifest mismatch'):
                    module.analyze(run)
        with tempfile.TemporaryDirectory() as folder:
            run = self.frozen_run(folder)
            (run / 'summary.json').unlink()
            self.manifest(run)
            with self.assertRaisesRegex(ValueError, 'bind all required'):
                module.analyze(run)

    def test_external_manifest_path_and_symlink_are_rejected(self):
        for name in ('../external', '/external', 'external-link'):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                run = self.frozen_run(folder)
                external = Path(folder) / 'external'
                external.write_text('external')
                if name == 'external-link':
                    (run / name).symlink_to(external)
                manifest = json.loads((run / 'hashes.json').read_text())
                manifest[name] = module.sha(external)
                (run / 'hashes.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, 'unsafe path'):
                    module.analyze(run)

    def test_failed_cleanup_and_unrequested_trace_cannot_be_reclassified(self):
        for name, value in (('cleanup.json', {'restored': False, 'cleanup_errors': []}),
                            ('provenance.json', {'candidate_trace': False})):
            with self.subTest(name=name), tempfile.TemporaryDirectory() as folder:
                run = self.frozen_run(folder)
                (run / name).write_text(json.dumps(value))
                self.manifest(run)
                with self.assertRaises(ValueError):
                    module.analyze(run)

    def test_output_inside_original_or_existing_receipt_is_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            run = self.frozen_run(folder)
            for output in (run / 'reanalysis.json', Path(folder) / 'existing.json'):
                if not output.is_relative_to(run):
                    output.write_text('keep')
                result = subprocess.run([sys.executable, '-B', str(SCRIPT), str(run), '--out', str(output)],
                                        capture_output=True, text=True)
                self.assertNotEqual(result.returncode, 0)
            self.assertFalse((run / 'reanalysis.json').exists())
            self.assertEqual((Path(folder) / 'existing.json').read_text(), 'keep')

