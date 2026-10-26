# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery launcher policy without access to the real user systemd manager."""
import importlib.machinery
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('dome_keepalive', str(ROOT/'fork/dome-keepalive.py'))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec); loader.exec_module(helper)

class DomeKeepalive(unittest.TestCase):
    def test_graphical_session_and_backend_environment(self):
        with patch.object(helper.subprocess, 'run') as run, patch.object(helper.shutil, 'which', return_value='/opt/dome/bin/dome-compositor'):
            run.return_value.returncode = 0
            env = {'WAYLAND_DISPLAY':'host-wayland','DISPLAY':':1','WAYLAND_SOCKET':'9'}
            self.assertEqual(helper.command(env, {'backend':'drm'}), ['/opt/dome/bin/dome-compositor','--backend','drm'])
            self.assertNotIn('WAYLAND_SOCKET',env)
            self.assertNotIn('WAYLAND_DISPLAY',env)
            self.assertEqual(env['DOME_START_XR'],'1')
            self.assertEqual(env['DOME_LOGIN_SESSION'],'0')
            self.assertIsNone(helper.command({}, {'backend':'nested'}))
            self.assertIsNotNone(helper.command({'DISPLAY':':1'}, {}))
            run.return_value.returncode = 1
            self.assertIsNone(helper.command({'DISPLAY':':1'}, {}))
    def test_stops_and_existing_owner_never_spawn(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}), patch.object(helper.sys,'argv',['helper']), patch.object(helper.subprocess,'run') as run, patch.object(helper.os,'execvpe') as execute:
            root=Path(directory)/'dome';root.mkdir();marker=root/'explicit-stop';marker.write_text('stop')
            self.assertEqual(helper.main(),0);run.assert_not_called();execute.assert_not_called()
            marker.unlink();run.return_value.returncode=0;run.return_value.stdout='(true,)'
            self.assertEqual(helper.main(),0);execute.assert_not_called()
    def test_resume_clears_only_marker_and_starts_unit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}), patch.object(helper.sys,'argv',['helper','--resume']), patch.object(helper.subprocess,'call',return_value=0) as call:
            root=Path(directory)/'dome';root.mkdir();marker=root/'explicit-stop';marker.touch()
            self.assertEqual(helper.main(),0);self.assertFalse(marker.exists())
            call.assert_called_once_with(['systemctl','--user','start','wivrn-pro-dome.service'])
