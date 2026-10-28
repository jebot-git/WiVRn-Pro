# SPDX-License-Identifier: GPL-3.0-or-later
"""Recovery policy without a real compositor, focus change or systemd manager."""
import fcntl
import importlib.machinery
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
loader = importlib.machinery.SourceFileLoader('dome_keepalive', str(ROOT/'fork/dome-keepalive.py'))
spec = importlib.util.spec_from_loader(loader.name, loader)
helper = importlib.util.module_from_spec(spec)
loader.exec_module(helper)


class DomeKeepalive(unittest.TestCase):
    def test_native_recovery_retains_desktop_or_xr_choice(self):
        with patch.object(helper.subprocess, 'run', return_value=Mock(returncode=0)), patch.object(helper.shutil, 'which', return_value='/opt/dome/bin/dome-compositor'):
            for active, shown, start in [(False, False, '0'), (False, True, '0'), (True, False, '0'), (True, True, '1'), ('true', True, '0'), (True, 'true', '0')]:
                env = {'XDG_SESSION_DESKTOP':'dome', 'WAYLAND_DISPLAY':'host', 'DISPLAY':':1', 'WAYLAND_SOCKET':'9', 'DOME_START_XR':'1'}
                self.assertEqual(helper.command(env, {'version':1, 'backend':'drm', 'xr_active':active, 'shown':shown}), ['/opt/dome/bin/dome-compositor','--backend','drm'])
                self.assertFalse({'DISPLAY','WAYLAND_DISPLAY','WAYLAND_SOCKET'} & env.keys())
                self.assertEqual(env['DOME_START_XR'], start)
                self.assertEqual(env['DOME_LOGIN_SESSION'], '0')
            env = {'XDG_SESSION_DESKTOP':'dome'}
            helper.command(env, {'version':1,'backend':'drm'})
            self.assertEqual(env['DOME_START_XR'], '0')

    def test_background_never_starts_nested_or_unknown_context(self):
        with patch.object(helper.subprocess, 'run') as run:
            for context in [{}, [], None, {'version':1,'backend':'nested'}, {'version':2,'backend':'drm'}, {'backend':'drm'}]:
                self.assertIsNone(helper.command({'XDG_SESSION_DESKTOP':'dome','DISPLAY':':1'}, context))
            for desktop in ['', 'KDE', 'gnome']:
                self.assertIsNone(helper.command({'XDG_SESSION_DESKTOP':desktop}, {'version':1,'backend':'drm'}))
            run.assert_not_called()

    def test_inactive_graphical_session_does_not_recover(self):
        with patch.object(helper.subprocess, 'run', return_value=Mock(returncode=1)), patch.object(helper.shutil, 'which', return_value='/opt/dome/bin/dome-compositor'):
            self.assertIsNone(helper.command({'XDG_SESSION_DESKTOP':'dome'}, {'version':1,'backend':'drm'}))

    def test_native_lock_present_and_absent(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            self.assertFalse(helper.native_owner_active(root))
            with (root/'native-owner.lock').open('w') as lock:
                self.assertFalse(helper.native_owner_active(root))
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.assertTrue(helper.native_owner_active(root))
            self.assertFalse(helper.native_owner_active(root))
            (root/'native-owner.lock').unlink()
            (root/'native-owner.lock').symlink_to(root/'missing')
            self.assertTrue(helper.native_owner_active(root))

    def test_stops_and_existing_owner_never_spawn(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}), patch.object(helper.sys,'argv',['helper']), patch.object(helper.subprocess,'run') as run, patch.object(helper.os,'execvpe') as execute:
            root=Path(directory)/'dome';root.mkdir();marker=root/'explicit-stop';marker.touch()
            self.assertEqual(helper.main(),0);run.assert_not_called();execute.assert_not_called()
            marker.unlink();run.return_value=Mock(returncode=0,stdout='(true,)')
            self.assertEqual(helper.main(),0);execute.assert_not_called()
            with (root/'native-owner.lock').open('w') as lock:
                fcntl.flock(lock,fcntl.LOCK_EX | fcntl.LOCK_NB);run.reset_mock()
                self.assertEqual(helper.main(),0);run.assert_not_called();execute.assert_not_called()

    def test_bus_failure_does_not_spawn(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}), patch.object(helper.sys,'argv',['helper']), patch.object(helper.os,'execvpe') as execute:
            for reply in [Mock(returncode=1,stdout=''), Mock(returncode=0,stdout='unexpected')]:
                with patch.object(helper.subprocess,'run',return_value=reply):
                    self.assertEqual(helper.main(),0)
            with patch.object(helper.subprocess,'run',side_effect=subprocess.TimeoutExpired('gdbus',3)):
                self.assertEqual(helper.main(),0)
            execute.assert_not_called()

    def test_missing_or_bad_context_does_not_spawn(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}), patch.object(helper.sys,'argv',['helper']), patch.object(helper.subprocess,'run',return_value=Mock(returncode=0,stdout='(false,)')), patch.object(helper.os,'execvpe') as execute:
            root=Path(directory)/'dome';root.mkdir()
            self.assertEqual(helper.main(),0)
            for value in ['bad JSON', '[]', json.dumps({'version':1,'backend':'nested'})]:
                (root/'wivrn-context.json').write_text(value)
                self.assertEqual(helper.main(),0)
            execute.assert_not_called()

    def test_resume_clears_only_marker_and_starts_unit(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory}), patch.object(helper.sys,'argv',['helper','--resume']), patch.object(helper.subprocess,'call',return_value=0) as call:
            root=Path(directory)/'dome';root.mkdir();marker=root/'explicit-stop';marker.touch()
            self.assertEqual(helper.main(),0);self.assertFalse(marker.exists())
            call.assert_called_once_with(['systemctl','--user','start','wivrn-pro-dome.service'])

    def test_native_fallback_executes_with_observed_choice(self):
        for active, shown, expected in [(False,False,'0'), (True,True,'1'), (True,False,'0')]:
            with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory,'XDG_SESSION_DESKTOP':'dome'}, clear=True), patch.object(helper.sys,'argv',['helper']), patch.object(helper.shutil,'which',return_value='/opt/dome/bin/dome-compositor'), patch.object(helper.signal,'pthread_sigmask'), patch.object(helper.os,'execvpe') as execute:
                root=Path(directory)/'dome';root.mkdir()
                (root/'wivrn-context.json').write_text(json.dumps({'version':1,'backend':'drm','xr_active':active,'shown':shown}))
                with patch.object(helper.subprocess,'run',side_effect=[Mock(returncode=0,stdout='(false,)'), Mock(returncode=0)]):
                    helper.main()
                execute.assert_called_once()
                program,args,env=execute.call_args.args
                self.assertEqual(args,[program,'--backend','drm'])
                self.assertEqual(env['DOME_START_XR'],expected)

    def test_an_owner_acquired_during_decision_cancels_fallback(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {'XDG_RUNTIME_DIR':directory,'XDG_SESSION_DESKTOP':'dome'}, clear=True), patch.object(helper.sys,'argv',['helper']), patch.object(helper.shutil,'which',return_value='/opt/dome/bin/dome-compositor'), patch.object(helper.os,'execvpe') as execute:
            root=Path(directory)/'dome';root.mkdir()
            (root/'wivrn-context.json').write_text(json.dumps({'version':1,'backend':'drm'}))
            with patch.object(helper.subprocess,'run',side_effect=[Mock(returncode=0,stdout='(false,)'), Mock(returncode=0)]), patch.object(helper,'native_owner_active',side_effect=[False,True]):
                self.assertEqual(helper.main(),0)
            execute.assert_not_called()
