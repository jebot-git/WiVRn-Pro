import copy
import importlib.util
import json
from argparse import Namespace
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_publication_trace import fixture
from test_selection_counterfactual import sources

spec = importlib.util.spec_from_file_location('publication_capture', Path(__file__).resolve().parents[1]/'integration/native_compare.py')
compare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(compare)


def settings():
    return Namespace(mode='custom-vr444', decoder_backend='cpu-neon', candidate_trace=True,
        source_trace=True, publication_trace=True, cpu_output='cached-copy', selection_policy='closest-target')


def write_fixture(output):
    ledger, traces, presented = fixture()
    for row in presented:
        row.update(publication_trace_enabled=True, publication_trace_records=5, publication_trace_epoch=1,
            publication_trace_dropped=0, publication_trace_valid=True, publication_trace_write_ok=True)
    for name, rows in [('client/codec-test/publication-trace.jsonl', ledger),
                       ('client/codec-test/selection-trace.jsonl', traces),
                       ('client/codec-test-present.jsonl', presented), ('host/source-trace.jsonl', sources(frames=(1, 2)))]:
        path = output / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    provenance = vars(settings()) | {'source_helper_sha256': {name: '1'*64 for name in compare.publication_trace_helpers(settings())}}
    (output/'provenance.json').write_text(json.dumps(provenance))
    (output/'client-config-readback.json').write_text(json.dumps({'codec-test-publication-trace.txt': '1\n'}))


class PublicationCaptureTests(unittest.TestCase):
    def test_opt_in_controls_and_backup_contract(self):
        args = settings()
        self.assertEqual(compare.experimental_settings(args)['codec-test-publication-trace.txt'], '1')
        self.assertIn('codec-test-publication-trace.txt', compare.CONFIG_FILES)
        self.assertEqual(compare.experimental_settings(Namespace(**(vars(args) | {'publication_trace': False})))['codec-test-publication-trace.txt'], '0')
        for change in ({'mode': 'native-hevc'}, {'decoder_backend': 'resident'}, {'candidate_trace': False},
                       {'source_trace': False}, {'publication_trace': 1}):
            with self.assertRaises(ValueError): compare.publication_trace_helpers(Namespace(**(vars(args) | change)))
        self.assertEqual(compare.publication_trace_helpers(Namespace(publication_trace=False), Path('/absent')), ())

    def test_source_preflight_precedes_every_device_command_and_output_creation(self):
        with tempfile.TemporaryDirectory() as folder:
            args = settings()
            vars(args).update(seconds=30, port=9767, fps_divider=1, bitrate=20_000_000, q=8, compression='lz4',
                selection_phase='early', source=Path(folder)/'not-integrated', out=Path(folder)/'unused', serial='never-queried')
            with patch.object(compare, 'command') as device, patch.object(compare.subprocess, 'run') as command:
                with self.assertRaisesRegex(ValueError, 'overlay is absent'): compare.run(args)
                device.assert_not_called(); command.assert_not_called()
                self.assertFalse(args.out.exists())
            for name in compare.publication_trace_helpers(args):
                path = args.source/name; path.parent.mkdir(parents=True, exist_ok=True); path.write_text('fixture')
            self.assertEqual(len(compare.publication_trace_helpers(args, args.source)), 3)

    def test_checkpoint_config_and_all_publication_source_joins_are_required(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder)
            self.assertEqual(compare.evaluate_publication_trace(output, False)['status'], 'off')
            self.assertEqual(compare.evaluate_publication_trace(output, True)['status'], 'fail')
            self.assertEqual(compare.evaluate_publication_trace(output, 1)['status'], 'fail')
            write_fixture(output)
            result = compare.evaluate_publication_trace(output, True)
            self.assertEqual(result['status'], 'pass', result)
            self.assertEqual(result['published_source_join']['analyzed_frames'], 2)
            self.assertFalse(result['promotion_eligible'])
            self.assertEqual(compare.evaluate_publication_trace(output, False)['status'], 'fail')
            # Only frame 1 is selected; missing frame 2 must still fail, because
            # it is in the complete publication prefix being claimed.
            (output/'host/source-trace.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in sources(frames=(1,))))
            result = compare.evaluate_publication_trace(output, True)
            self.assertEqual(result['status'], 'fail')
            self.assertIn('not every published frame', result['errors'][0])

    def test_forged_capture_metadata_never_relabels_prefix_as_valid(self):
        for mutation in ('provenance', 'source_hash', 'readback', 'write', 'epoch', 'count', 'bool', 'missing_ledger', 'missing_candidate'):
            with tempfile.TemporaryDirectory() as folder:
                output = Path(folder); write_fixture(output)
                if mutation in ('provenance', 'source_hash'):
                    path = output/'provenance.json'; value = json.loads(path.read_text())
                    if mutation == 'provenance': value['publication_trace'] = False
                    else: value['source_helper_sha256'].pop('common/codec_test/publication_ledger.h')
                    path.write_text(json.dumps(value))
                elif mutation == 'readback':
                    (output/'client-config-readback.json').write_text(json.dumps({'codec-test-publication-trace.txt': '0\n'}))
                elif mutation.startswith('missing_'):
                    (output/('client/codec-test/publication-trace.jsonl' if mutation == 'missing_ledger' else 'client/codec-test/selection-trace.jsonl')).unlink()
                else:
                    path = output/'client/codec-test-present.jsonl'; row = json.loads(path.read_text())
                    changes = {'write': ('publication_trace_write_ok', False), 'epoch': ('publication_trace_epoch', 2),
                               'count': ('publication_trace_records', 4), 'bool': ('publication_trace_valid', 1)}
                    key, value = changes[mutation]; row[key] = value; path.write_text(json.dumps(row)+'\n')
                with self.subTest(mutation=mutation):
                    self.assertEqual(compare.evaluate_publication_trace(output, True)['status'], 'fail')

    def test_presentation_fields_alone_reveal_unrequested_diagnostic(self):
        with tempfile.TemporaryDirectory() as folder:
            output = Path(folder); write_fixture(output)
            (output/'client/codec-test/publication-trace.jsonl').unlink()
            self.assertEqual(compare.evaluate_publication_trace(output, False)['status'], 'fail')
