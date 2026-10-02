# SPDX-License-Identifier: GPL-3.0-or-later
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QApplication
from launcher import ConnectionWindow, migrate_settings, wayvr_arguments

class LauncherTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.new = QSettings(str(Path(self.temp.name)/'new.ini'),QSettings.Format.IniFormat)
        self.old = QSettings(str(Path(self.temp.name)/'old.ini'),QSettings.Format.IniFormat)
        self.old.setValue('host','legacy.local')
        self.old.setValue('user','legacyuser')
        self.old.sync()
    def tearDown(self): self.temp.cleanup()
    def test_settings_migrate_once_without_overwriting_existing_target(self):
        self.new.setValue('host','chosen.local')
        migrate_settings(self.new,self.old)
        self.assertEqual(self.new.value('host'),'chosen.local')
        self.assertEqual(self.new.value('user'),'legacyuser')
        self.old.setValue('user','changed')
        migrate_settings(self.new,self.old)
        self.assertEqual(self.new.value('user'),'legacyuser')
        self.assertEqual(self.old.value('host'),'legacy.local')
    def test_fresh_app_without_wayvr_can_open_normal_receiver(self):
        self.new.setValue('migration_complete',True)
        with patch('launcher.find_wayvr',return_value=None):
            window = ConnectionWindow(self.new)
        self.assertFalse(window.vr.isEnabled())
        window.host.setText('mac.local'); window.user.setText('user')
        with patch('launcher.Relay') as relay:
            window.open_desktop()
            relay.assert_called_once_with('mac.local','user')
            relay.return_value.show.assert_called_once()
        window.deleteLater()
    def test_invalid_target_does_not_spawn_or_overwrite_profile(self):
        self.new.setValue('migration_complete',True)
        window = ConnectionWindow(self.new)
        window.host.setText('-bad'); window.user.setText('user')
        with patch('launcher.Relay') as relay:
            window.open_desktop()
            relay.assert_not_called()
        self.assertFalse(self.new.contains('host'))
        window.deleteLater()
    def test_wayvr_arguments_use_independent_application(self):
        args=wayvr_arguments('/opt/relay/bin/mac-desktop-relay','icon.svg','mac.local','user')
        self.assertIn('/opt/relay/bin/mac-desktop-relay',args)
        self.assertEqual(args[-2],'--')
        self.assertEqual(args[-1],'--connect --host mac.local --user user')

if __name__ == '__main__': unittest.main()
