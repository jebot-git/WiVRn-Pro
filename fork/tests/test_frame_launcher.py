# SPDX-License-Identifier: GPL-3.0-or-later
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


LAUNCHER = Path(__file__).resolve().parents[1] / 'packaging/launch-wivrn-pro'


class FrameLauncherTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='frame launcher ')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.os_release = self.root / 'os-release'
        self.os_release.write_text('ID=steamos\n')
        self.launcher = self.root / 'launch-wivrn-pro'
        # Substitute only the OS fixture; execute the shipped shell logic.
        self.launcher.write_text(LAUNCHER.read_text().replace(
            '/etc/os-release', '"' + str(self.os_release) + '"'))
        self.launcher.chmod(0o755)
        self.env = {'HOME': str(self.root), 'PATH': os.defpath}

    def run_launcher(self, *args):
        return subprocess.run([str(self.launcher), *args], env=self.env,
                              text=True, capture_output=True, timeout=5)

    def runtime(self, path, body, architecture='aarch64-linux-gnu', machine=183):
        path.mkdir(parents=True)
        run = path / 'run'
        run.write_text('#!/bin/sh\nset -eu\nexport PRESSURE_VESSEL_ARCHITECTURES=' + architecture + '\n' + body)
        run.chmod(0o755)
        helper = path / 'pressure-vessel/bin/pressure-vessel-wrap'
        helper.parent.mkdir(parents=True)
        helper.write_bytes(b'\x7fELF' + bytes(14) + machine.to_bytes(2, 'little'))
        return path

    def test_missing_runtime_is_actionable(self):
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('Steam Linux Runtime 4.0 (ARM64)', result.stderr)

    def test_discovery_preserves_arguments_and_does_not_inject_bundle_on_host(self):
        self.runtime(self.root / '.steam/root/steamapps/common/SteamLinuxRuntime_4-arm64',
                     'test -z "${LD_LIBRARY_PATH:-}"\n'
                     'test "$PRESSURE_VESSEL_IMPORT_OPENXR_1_RUNTIMES" = 1\n'
                     'printf "%s\\n" "$@"\nexit 23\n')
        result = self.run_launcher('two words', '', '--help')
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         ['--', str(self.launcher), 'two words', '', '--help'])

    def test_runtime_that_does_not_enter_container_fails_without_loop(self):
        runtime = self.runtime(self.root / 'custom runtime', 'shift\nexec "$@"\n')
        self.env['WIVRN_STEAM_RUNTIME'] = str(runtime)
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('did not enter Steam Runtime 4', result.stderr)

    def test_already_inside_runtime_executes_client(self):
        self.os_release.write_text('VERSION_CODENAME="steamrt4"\nVARIANT_ID="com.valvesoftware.steamruntime.platform-arm64-steamrt4"\n')
        binary = self.root / 'usr/bin/wivrn'
        binary.parent.mkdir(parents=True)
        binary.write_text('#!/bin/sh\nprintf "%s\\n" "$LD_LIBRARY_PATH" "$@"\nexit 19\n')
        binary.chmod(0o755)
        self.env['LD_LIBRARY_PATH'] = '/existing'
        self.env['WIVRN_FRAME_ENTERING_RUNTIME'] = '1'
        result = self.run_launcher('two words')
        self.assertEqual(result.returncode, 19, result.stderr)
        self.assertEqual(result.stdout.splitlines(),
                         [str(self.root / 'usr/lib') + ':/existing', 'two words'])

    def test_x86_installation_is_not_auto_selected(self):
        self.runtime(self.root / '.steam/root/steamapps/common/SteamLinuxRuntime_4',
                     'exit 99\n', architecture='x86_64-linux-gnu:i386-linux-gnu', machine=62)
        result = self.run_launcher()
        self.assertNotEqual(result.returncode, 99)
        self.assertIn('requires Steam Linux Runtime', result.stderr)

    def test_explicit_x86_userspace_rejected_even_with_arm_helper(self):
        runtime = self.runtime(self.root / 'mixed runtime', 'exit 99\n',
                               architecture='x86_64-linux-gnu:i386-linux-gnu')
        self.env['WIVRN_STEAM_RUNTIME'] = str(runtime)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('not a complete native ARM64', result.stderr)

    def test_x86_helper_rejected_even_with_arm_userspace(self):
        runtime = self.runtime(self.root / 'wrong helper', 'exit 99\n', machine=62)
        self.env['WIVRN_STEAM_RUNTIME'] = str(runtime)
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('not a complete native ARM64', result.stderr)

    def test_bundled_runtime_wins_and_overrides_foreign_helper_prefix(self):
        runtime = self.runtime(self.root / 'SteamLinuxRuntime_4-arm64',
                               'printf "%s\\n" "$PRESSURE_VESSEL_PREFIX"\nexit 23\n')
        self.env['PRESSURE_VESSEL_PREFIX'] = '/foreign/pressure-vessel'
        result = self.run_launcher()
        self.assertEqual(result.returncode, 23, result.stderr)
        self.assertEqual(result.stdout.strip(), str(runtime / 'pressure-vessel'))

    def test_already_inside_x86_runtime_is_rejected(self):
        self.os_release.write_text('VERSION_CODENAME=steamrt4\nVARIANT_ID="com.valvesoftware.steamruntime.platform-amd64_i386-steamrt4"\n')
        result = self.run_launcher()
        self.assertEqual(result.returncode, 1)
        self.assertIn('userspace is not ARM64', result.stderr)


if __name__ == '__main__':
    unittest.main()
