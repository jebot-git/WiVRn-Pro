import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import contextlib
import io
import sys
spec = importlib.util.spec_from_file_location('live', Path(__file__).resolve().parents[1]/'integration/live.py')
live = importlib.util.module_from_spec(spec); spec.loader.exec_module(live)

class MetaSR(unittest.TestCase):
    def test_config_resets_both_filters_and_modes_exclusive(self):
        for mode, flag in [('off',0),('normal',4),('quality',8)]:
            self.assertEqual(live.meta_sr_config(mode), {'super_sampling':0,'sharpening':flag})
        with self.assertRaises(KeyError): live.meta_sr_config('normal+quality')
    def test_missing_fresh_telemetry_unverified(self):
        self.assertEqual(live.meta_sr_metrics([], 'quality')['status'], 'unverified')
    def test_unsupported_is_not_success(self):
        row=dict(requested_flags=8,submitted_flags=0,extension_enabled=False,projection_submitted=True)
        self.assertEqual(live.meta_sr_metrics([row], 'quality')['status'], 'unsupported')
    def test_actual_submission_required_and_effect_never_claimed(self):
        row=dict(requested_flags=8,submitted_flags=8,extension_enabled=True,projection_submitted=True)
        result=live.meta_sr_metrics([row], 'quality')
        self.assertEqual(result['status'],'submitted'); self.assertFalse(result['runtime_effect_verified'])
        row['submitted_flags']=4
        self.assertEqual(live.meta_sr_metrics([row], 'quality')['status'],'mismatch')
    def test_no_projection_is_not_submission(self):
        row=dict(requested_flags=8,submitted_flags=0,extension_enabled=True,projection_submitted=False)
        self.assertEqual(live.meta_sr_metrics([row], 'quality')['status'],'unverified')
    def test_off_without_extension_valid(self):
        row=dict(requested_flags=0,submitted_flags=0,extension_enabled=False,projection_submitted=True)
        self.assertEqual(live.meta_sr_metrics([row], 'off')['status'],'off')
    def test_suite_entrypoint_forwards_filter_and_defaults_off(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
        import suite
        for extra, expected in [([], 'off'), (['--meta-sr','quality'], 'quality')]:
            args=['suite.py','live-integration','--source','/unused','--serial','test',
                  '--out','/unused-output','--mode','vulkan-vr444']+extra
            with patch.object(sys,'argv',args), patch('integration.live.run',return_value={'status':'pass'}) as run, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(suite.main(),0)
            self.assertEqual(run.call_args.args[0].meta_sr,expected)
