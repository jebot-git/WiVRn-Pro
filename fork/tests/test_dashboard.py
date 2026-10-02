# SPDX-License-Identifier: GPL-3.0-or-later
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import tomllib
import unittest
os.environ['QT_QPA_PLATFORM'] = 'offscreen'
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'dashboard/shora'))
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
from wivrn_dashboard import Dashboard, StreamingPanel, atomic_save, merge_tuning
from shora_qt import STYLE

class FakeServer(QObject):
    state = Signal(dict)
    failure = Signal(str)
    saved = Signal()
    def __init__(self): super().__init__(); self.calls = []
    def save(self, value, expected): self.calls.append(('save', value, expected))
    def service(self, action): self.calls.append(('service', action))
    def method(self, *args): self.calls.append(args)

class DashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion"); cls.app.setStyleSheet(STYLE)
    def setUp(self):
        self.server = FakeServer(); self.panel = StreamingPanel(self.server)
        self.config = {'encoder': [{'encoder':'vaapi', 'codec':'h265', 'options':{'quality':'balanced'}}, {'encoder':'vulkan'}],
                       'application':['custom-app','--flag'], 'bitrate':72000000, 'scale':[0.9,0.8],
                       'unknown-future':{'keep':[1,2,3]}, 'openvr-compat-path':None}
        self.server.state.emit({'JsonConfiguration':json.dumps(self.config), 'KnownKeys':[['Quest Pro','test-key',0]]})
    def tearDown(self): self.panel.close()
    def test_fresh_server_null_configuration_is_editable(self):
        self.server.state.emit({'JsonConfiguration':'null'})
        self.assertEqual(self.panel.configuration(),{})
        self.panel.tcp.setChecked(True); self.panel.save()
        self.assertEqual(self.server.calls[-1],('save',{'tcp-only':True},{}))
    def test_noop_is_lossless(self): self.assertEqual(self.panel.configuration(), self.config)
    def test_checkbox_preserves_encoder_layout_and_unknown_values(self):
        self.panel.tcp.setChecked(True)
        expected = copy.deepcopy(self.config); expected['tcp-only'] = True
        self.assertEqual(self.panel.configuration(), expected)
    def test_raw_edits_arent_overridden_by_unchanged_controls(self):
        value = copy.deepcopy(self.config); value['port']=9876; value['tcp-only']=True
        self.panel.raw.setPlainText(json.dumps(value)); self.assertEqual(self.panel.configuration(), value)
    def test_codec_preserves_encoder_options(self):
        self.panel.codec.setCurrentText('h264')
        value = self.panel.configuration()
        self.assertEqual(value['encoder'][0]['options'], {'quality':'balanced'})
        self.assertEqual(value['encoder'][1], {'encoder':'vulkan','codec':'h264'})
    def test_refresh_does_not_erase_draft(self):
        self.panel.hostname.setText('local-draft'); self.panel.changed()
        self.server.state.emit({'JsonConfiguration':'{"hostname":"external"}'})
        self.assertEqual(self.panel.hostname.text(), 'local-draft')
        self.assertEqual(self.panel.original, self.config)
    def test_wayvr_defaults_do_not_rewrite_existing_config(self):
        self.assertTrue(self.panel.wayvr.isChecked())
        self.assertTrue(self.panel.wayvr_dashboard.isChecked())
        self.assertNotIn('wayvr', self.panel.configuration())
    def test_wayvr_controls_preserve_args_and_legacy_application(self):
        config = copy.deepcopy(self.config)
        config['application'] = ['/home/me/WayVR-v26.8.0-x86_64.AppImage', '--show']
        config['wayvr'] = {'enabled':True, 'args':['--no-audio'], 'future':42}
        self.server.state.emit({'JsonConfiguration':json.dumps(config), 'SessionRunning':True, 'WayVRStatus':'Worker running'})
        self.panel.wayvr_dashboard.setChecked(False)
        result = self.panel.configuration()
        self.assertEqual(result['wayvr'], {**config['wayvr'], 'dashboard':False})
        self.assertEqual(result['application'], config['application'])
        self.assertIn('Worker running', self.panel.wayvr_status.text())
        self.panel.wayvr_restart.click()
        self.assertEqual(self.server.calls[-1], ('RestartWayVR',))
    def test_pairing_controls(self):
        self.panel.revoke_key(); self.assertEqual(self.server.calls[-1], ('RevokeKey','test-key'))
    def test_tuning_preserves_devices_osc_and_assignments(self):
        text='''# local profile
height_m=1.9
custom="keep me"
[[tracker_assignments]]
mac="AA:BB:CC:DD:EE:FF"
position=9
[face]
enabled=true
source="openxr"
destination="127.0.0.1:9123"
[haritorax]
enabled=true
ports=["/dev/fake"]
'''
        result=tomllib.loads(merge_tuning(text, {'height_m':1.85,'smoothing':0.5,'stay_aligned':{'enabled':False,'rate_deg_per_sec':0.2,'standing':None,'sitting':None,'flat':None}}))
        old=tomllib.loads(text)
        for key in ('custom','tracker_assignments','face','haritorax'): self.assertEqual(result[key],old[key])
        self.assertEqual(result['height_m'],1.85)
    def test_profile_external_change_blocks_save(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'shora.toml'
            atomic_save(path,'height_m=1.8\n',None)
            path.write_text('height_m=1.9\n')
            with self.assertRaises(ValueError): atomic_save(path,'height_m=1.7\n','height_m=1.8\n')
            self.assertEqual(path.read_text(),'height_m=1.9\n')
    def test_device_ui_preserves_profile_and_only_changes_selected_option(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'shora.toml'
            text = '# retain comment on no-op\nheight_m=1.9\n[haritorax]\nenabled=true\nbaud_rate=500000\n[face]\nenabled=true\nsource="openxr"\ndestination="127.0.0.1:9999"\n'
            path.write_text(text)
            window = Dashboard(self.server, path, Path(directory)/'control', attach=False)
            window.poll.stop()
            self.assertTrue(window.haritorax.isChecked()); self.assertEqual(window.face.currentIndex(),3)
            window.save_devices(); self.assertEqual(path.read_text(),text)
            window.haritorax.setChecked(False); window.save_devices()
            saved = tomllib.loads(path.read_text()); original = tomllib.loads(text)
            original['haritorax']['enabled'] = False
            self.assertEqual(saved,original)
            window.close()

    def test_window_reuses_tracking_ui_without_starting_service(self):
        with tempfile.TemporaryDirectory() as directory:
            window=Dashboard(self.server, Path(directory)/'shora.toml', Path(directory)/'control', attach=False)
            window.poll.stop(); window.show(); self.app.processEvents()
            self.assertEqual(window.tabs.tabText(0),'WiVRn streaming')
            self.assertTrue(window.binary.isHidden())
            self.assertFalse(window.settings.isHidden())
            window.grab().save('/tmp/wivrn-dashboard-preview.png')
            window.close()
            self.assertEqual(self.server.calls,[])

if __name__=='__main__': unittest.main()
