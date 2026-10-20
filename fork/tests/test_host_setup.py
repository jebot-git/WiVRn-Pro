#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib.util
import json
import os
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


check = load('host_check', 'fork/host-check.py')
setup = load('host_setup', 'fork/install-system.py')


class RuntimeInstallation(unittest.TestCase):
    def fixture(self, directory):
        prefix = Path(directory) / 'user-prefix'
        manifest = prefix / 'share/openxr/1/openxr_wivrn_pro.json'
        manifest.parent.mkdir(parents=True)
        library = prefix / 'lib64/wivrn/libopenxr_wivrn.so'
        library.parent.mkdir(parents=True)
        library.write_bytes(b'runtime')
        manifest.write_text(json.dumps({'file_format_version': '1.0.0', 'runtime': {
            'library_path': '../../../lib64/wivrn/libopenxr_wivrn.so'}}))
        return prefix, manifest, library

    def test_staged_runtime_is_self_contained_and_does_not_replace_active_selection(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix, manifest, library = self.fixture(directory)
            root = Path(directory) / 'system'
            old_umask = os.umask(0o022)
            try:
                installed = setup.install_runtime(prefix, root)
            finally:
                os.umask(old_umask)
            target = root / 'usr/local/share/openxr/1/openxr_wivrn_pro.json'
            value = json.loads(target.read_text())['runtime']['library_path']
            self.assertEqual((target.parent / value).resolve(), installed[0])
            self.assertEqual(installed[0].read_bytes(), library.read_bytes())
            self.assertEqual(target.stat().st_mode & 0o777, 0o644)
            self.assertFalse((root / 'etc/openxr/1/active_runtime.json').exists())
            self.assertEqual(json.loads(manifest.read_text())['runtime']['library_path'],
                             '../../../lib64/wivrn/libopenxr_wivrn.so')

    def test_reject_library_outside_prefix_before_installing(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix, manifest, library = self.fixture(directory)
            outside = Path(directory) / 'outside.so'
            outside.write_bytes(b'outside')
            library.unlink()
            library.symlink_to(outside)
            root = Path(directory) / 'system'
            with self.assertRaises(ValueError):
                setup.install_runtime(prefix, root)
            self.assertFalse(root.exists())

    def test_detect_mismatched_system_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            prefix, manifest, library = self.fixture(directory)
            target = Path(directory) / 'system'
            setup.install_runtime(prefix, target)
            installed = target / 'usr/local/share/openxr/1/openxr_wivrn_pro.json'
            with patch.object(check.ctypes, 'CDLL'):
                check.runtime_check(installed, matching=manifest)
                library.write_bytes(b'updated')
                with self.assertRaisesRegex(RuntimeError, 'differs'):
                    check.runtime_check(installed, matching=manifest)


class Availability(unittest.TestCase):
    def test_software_vulkan_is_not_a_hardware_gpu(self):
        with patch.object(check.Path, 'glob', return_value=[Path('/dev/dri/renderD128')]), \
             patch.object(check, 'open_device'), \
             patch.object(check, 'persistent_access', return_value=True), \
             patch.object(check, 'command', return_value='deviceType = PHYSICAL_DEVICE_TYPE_CPU'):
            with self.assertRaisesRegex(RuntimeError, 'hardware GPU'):
                check.gpu_check()

    def test_hardware_vulkan_probe_has_no_desktop_environment(self):
        with patch.object(check.Path, 'glob', return_value=[Path('/dev/dri/renderD128')]), \
             patch.object(check, 'open_device'), \
             patch.object(check, 'persistent_access', return_value=True), \
             patch.dict(os.environ, {'DISPLAY': ':99', 'WAYLAND_DISPLAY': 'wayland-stale'}), \
             patch.object(check, 'command', return_value='deviceType = PHYSICAL_DEVICE_TYPE_DISCRETE_GPU') as run:
            self.assertIn('hardware Vulkan', check.gpu_check())
            self.assertNotIn('DISPLAY', run.call_args.kwargs['env'])
            self.assertNotIn('WAYLAND_DISPLAY', run.call_args.kwargs['env'])

    def test_input_acl_does_not_prove_prelogin_access(self):
        info = os.stat_result((stat.S_IFCHR | 0o660, 0, 0, 1, 0, 987654, 0, 0, 0, 0))
        with patch.object(check, 'open_device', return_value=info), \
             patch.object(check.os, 'getuid', return_value=1000), \
             patch.object(check.os, 'getgid', return_value=1000), \
             patch.object(check.os, 'getgroups', return_value=[1000]):
            with self.assertRaisesRegex(RuntimeError, 'ACL only'):
                check.input_check()
            with patch.object(check.os, 'getgroups', return_value=[1000, 987654]):
                self.assertIn('persistent', check.input_check())

    def test_unavailable_pipewire_and_missing_probe_tools_are_reported(self):
        with patch.object(check.shutil, 'which', return_value=None):
            with self.assertRaisesRegex(RuntimeError, 'Install pw-cli'):
                check.command(['pw-cli', 'info', '0'])
        with patch.object(check.shutil, 'which', return_value='/usr/bin/pw-cli'), \
             patch.object(check.subprocess, 'run', side_effect=subprocess.TimeoutExpired('pw-cli', 8)):
            with self.assertRaises(subprocess.TimeoutExpired):
                check.command(['pw-cli', 'info', '0'])


if __name__ == '__main__':
    unittest.main()
