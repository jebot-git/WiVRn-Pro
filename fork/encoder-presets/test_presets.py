#!/usr/bin/env python3
"""Checks the config writer's preservation/refusal guarantees using temporary files."""
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parent
TOOL = ROOT / "wivrn-host-preset"


class PresetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.config = self.root / "config.json"
        self.original = {'application': ['steam', 'argument'], 'wayvr': {'args': ['--show']},
                         'bitrate': 123456, 'custom': [1, {'a': True}],
                         'encoder': [{'encoder': 'vaapi', 'options': {'coder': 'cavlc'}}]}
        self.original_bytes = json.dumps(self.original).encode()
        self.config.write_bytes(self.original_bytes)
        self.config.chmod(0o640)

    def run_tool(self, *args, success=True):
        env = dict(os.environ, XDG_CONFIG_HOME=str(self.root / "xdg"))
        proc = subprocess.run([str(TOOL), *map(str, args)], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode == 0, success, proc.stdout + proc.stderr)
        return proc

    def test_apply_preserves_unrelated_values_and_exact_backup(self):
        self.run_tool('apply', 'hevc-quality-150', '--config', self.config)
        saved = json.loads(self.config.read_text())
        for key in self.original.keys() - {'encoder'}:
            self.assertEqual(saved[key], self.original[key])
        self.assertEqual(saved['encoder']['codec'], 'h265')
        self.assertEqual(saved['encoder']['options']['gpb'], '0')
        self.assertNotIn('coder', saved['encoder']['options'])
        backups = list(self.root.glob('config.json.before-*'))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), self.original_bytes)
        self.assertEqual(stat.S_IMODE(self.config.stat().st_mode), 0o640)
        self.assertEqual(stat.S_IMODE(backups[0].stat().st_mode), 0o640)

    def test_render_does_not_change_base(self):
        output = self.root / 'rendered.json'
        self.run_tool('render', 'avc-ab-100', '--base', self.config, '--output', output)
        self.assertEqual(self.config.read_bytes(), self.original_bytes)
        self.assertEqual(json.loads(output.read_text())['encoder']['codec'], 'h264')
        self.run_tool('render', 'hevc-quality-150', '--base', self.config, '--output', output, success=False)
        self.assertEqual(json.loads(output.read_text())['encoder']['codec'], 'h264')

    def test_render_refuses_base_overwrite(self):
        self.run_tool('render', 'hevc-quality-150', '--base', self.config, '--output', self.config, success=False)
        self.assertEqual(self.config.read_bytes(), self.original_bytes)

    def test_invalid_json_is_preserved(self):
        for content in ('{"broken":', '[]'):
            self.config.write_text(content)
            self.run_tool('apply', 'hevc-balanced-100', '--config', self.config, success=False)
            self.assertEqual(self.config.read_text(), content)
        self.assertEqual(list(self.root.glob('config.json.before-*')), [])

    def test_refuses_symlink(self):
        link = self.root / 'link.json'
        link.symlink_to(self.config)
        self.run_tool('apply', 'hevc-balanced-100', '--config', link, success=False)
        self.assertEqual(self.config.read_bytes(), self.original_bytes)

    def test_cqp_requires_capable_target(self):
        self.run_tool('apply', 'hevc-cqp-24-experimental', '--config', self.config, success=False)
        self.assertEqual(self.config.read_bytes(), self.original_bytes)
        self.run_tool('apply', 'hevc-cqp-24-experimental', '--config', self.config, '--cqp-capable')
        options = json.loads(self.config.read_text())['encoder']['options']
        self.assertEqual(options['rate_control'], 'cqp')
        self.assertEqual(options['qp'], '24')
        self.assertEqual(options['low_delay_brc'], '0')

    def test_new_config_is_private(self):
        target = self.root / 'new' / 'config.json'
        self.run_tool('apply', 'hevc-balanced-100', '--config', target)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_all_profile_options_exist_in_installed_ffmpeg(self):
        help_text = {}
        for codec in ('h264', 'hevc'):
            result = subprocess.run(['ffmpeg', '-hide_banner', '-h', f'encoder={codec}_qsv'], capture_output=True, text=True, check=True)
            help_text[codec] = result.stdout + result.stderr
        for path in (ROOT / 'profiles').glob('*.json'):
            profile = json.loads(path.read_text())
            encoder = profile['host_config']['encoder']
            codec = 'hevc' if encoder['codec'] == 'h265' else 'h264'
            for option, value in encoder['options'].items():
                self.assertIsInstance(value, str)
                if option not in ('rate_control', 'qp'):
                    self.assertIn('-' + option + ' ', help_text[codec], str(path))
            self.assertEqual(profile['host_config']['bit-depth'], 8)
            self.assertNotIn('bitrate', profile['host_config'])
            if not profile['requires_cqp_branch']:
                rates = profile['expected_target_mbps']
                self.assertAlmostEqual(rates['left_eye'] + rates['right_eye'] + rates['alpha'], profile['headset_settings']['bitrate_mbps'], places=5)


if __name__ == '__main__':
    unittest.main(verbosity=2)
