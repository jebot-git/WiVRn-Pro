import copy
import os
from pathlib import Path
import sys
import unittest
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'dashboard/shora'))
from PySide6.QtWidgets import QApplication
from haritorax_panel import HaritoraXPanel

class SensorPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def test_reports_edits_staleness_and_explicit_apply(self):
        panel = HaritoraXPanel(); commands = []; panel.command_requested.connect(commands.append)
        self.assertFalse(panel.apply.isEnabled())
        snapshot = {'ports':[{'path':'fake','connected':True,'trackers':[{'name':'leftAnkle','channel':0,'mac':[1]*6,
            'settings_age_ms':20,'settings':{'sensor_mode':2,'fps':50,'accelerometer':True,'gyroscope':True,'magnetometer':False}}]}]}
        panel.update_snapshot(snapshot)
        self.assertEqual(panel.mode.currentData(), 2)
        panel.fps.setCurrentIndex(panel.fps.findData(100))
        panel.update_snapshot(snapshot)
        self.assertEqual(panel.fps.currentData(),100)
        stale = copy.deepcopy(snapshot); stale['ports'][0]['trackers'][0]['settings_age_ms']=6000
        panel.update_snapshot(stale); self.assertFalse(panel.apply.isEnabled())
        panel.update_snapshot(snapshot); self.assertTrue(panel.apply.isEnabled())
        panel.submit(); self.assertEqual(len(commands),1)
        self.assertEqual(commands[0]['hardware']['fps'],100)
        self.assertNotIn('communication_mode',commands[0]['hardware'])
        panel.update_snapshot(snapshot); self.assertTrue(panel.pending)
        self.assertFalse(panel.apply.isEnabled())
        panel.command_finished('set_haritora_settings',False,'Unconfirmed')
        self.assertIn('Unconfirmed',panel.notice.text()); self.assertTrue(panel.dirty)
        panel.load_report(); self.assertEqual(panel.fps.currentData(),50)
        panel.update_snapshot(None); self.assertFalse(panel.mode.isEnabled())
        panel.close()
