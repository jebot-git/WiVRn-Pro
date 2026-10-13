# SPDX-License-Identifier: GPL-3.0-or-later
import json
import unittest
import test_dashboard as base
from motoc_panel import MotocPanel
from shora_qt import ControlClient

class FrameDashboardTests(unittest.TestCase):
    setUpClass = classmethod(base.DashboardTests.setUpClass.__func__)
    setUp = base.DashboardTests.setUp
    tearDown = base.DashboardTests.tearDown

    def test_profiles_preserve_encoders_and_tracking(self):
        self.assertEqual(self.panel.configuration(), self.config)
        self.panel.headset_profile.setCurrentIndex(self.panel.headset_profile.findData('steam-frame'))
        value = self.panel.configuration()
        self.assertEqual(value, {**self.config, 'headset-profile': 'steam-frame'})
        self.panel.save()
        self.assertEqual(self.server.calls[-1], ('save', value, self.config))
        self.panel.dirty = False
        self.server.state.emit({'JsonConfiguration': json.dumps(value), 'ClientModel': 'steam-frame'})
        self.assertEqual(self.panel.headset_profile.currentData(), 'steam-frame')
        self.assertEqual(self.panel.configuration(), value)

    def test_frame_hides_anchor_mode_and_preserves_saved_quest_calibration(self):
        client = ControlClient()
        panel = MotocPanel(client, self.panel)
        state = {'tracking_mode': 'motoc', 'motoc': {'settings': {'enabled': True, 'mode': 'anchors', 'target': 'LHR-test'}}}
        try:
            panel.update_snapshot(state)
            self.assertEqual(panel.mode.currentData(), 'anchors')
            self.server.state.emit({'JsonConfiguration': json.dumps(self.config), 'ClientModel': 'steam-frame'})
            anchor = panel.mode.findData('anchors')
            self.assertTrue(panel.mode.view().isRowHidden(anchor))
            self.assertEqual(panel.mode.currentData(), 'once')
            self.assertFalse(panel.dirty)
            self.assertFalse(panel.buttons[0].isEnabled())
            panel.update_snapshot(state)
            self.assertEqual(panel.mode.currentData(), 'once')
            self.assertFalse(panel.buttons[1].isEnabled())
            self.server.state.emit({'JsonConfiguration': json.dumps(self.config), 'ClientModel': 'seacliff'})
            panel.update_snapshot(state)
            self.assertFalse(panel.mode.view().isRowHidden(anchor))
            self.assertEqual(panel.mode.currentData(), 'anchors')
            self.assertTrue(panel.buttons[0].isEnabled())
            self.assertEqual(panel.target.text(), 'LHR-test')
            panel.connection_changed(False)
            panel.headset_changed({'ClientModel': 'seacliff'})
            self.assertFalse(panel.buttons[0].isEnabled())
        finally:
            panel.close()
