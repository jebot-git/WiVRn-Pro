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
from PySide6.QtWidgets import QApplication, QScrollArea, QStyleOptionComboBox, QStyle
from PySide6.QtGui import QTextCursor
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
    def test_pyrowave_preset_preserves_codec_and_host_application(self):
        self.panel.preset_backend.setCurrentText('nvenc')
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('pyrowave-444-experimental'))
        self.panel.load_preset.click()
        value = self.panel.configuration()
        self.assertEqual(value['encoder'], {'encoder': 'pyrowave', 'codec': 'pyrowave'})
        self.assertEqual(value['application'], self.config['application'])
        self.assertFalse(self.panel.preset_backend.isEnabled())
        self.assertIn('Experimental', self.panel.preset_notice.text())

    def test_pyrowave_bitrate_saves_override_and_returns_to_headset(self):
        self.panel.encoder.setCurrentText('pyrowave')
        self.panel.codec.setCurrentText('pyrowave')
        self.panel.pyrowave_bitrate.setValue(350)
        self.panel.save()
        value = self.server.calls[-1][1]
        self.assertEqual(value['pyrowave-bitrate'], 350_000_000)
        self.assertEqual(value['bitrate'], self.config['bitrate'])
        self.assertEqual(value['application'], self.config['application'])
        self.panel.dirty = False
        self.server.state.emit({'JsonConfiguration': json.dumps(value)})
        self.assertEqual(self.panel.pyrowave_bitrate.value(), 350)
        self.panel.pyrowave_bitrate.setValue(0)
        self.assertNotIn('pyrowave-bitrate', self.panel.configuration())

    def test_pyrowave_preset_bitrate_is_editable_and_preserves_raw_edits(self):
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('pyrowave-444-experimental'))
        self.panel.load_preset.click()
        self.assertEqual(self.panel.pyrowave_bitrate.value(), 400)
        self.panel.pyrowave_bitrate.setValue(280)
        self.assertEqual(self.panel.configuration()['pyrowave-bitrate'], 280_000_000)
        self.panel.reload()
        draft = {**self.config, 'pyrowave-bitrate': 225_000_000}
        self.panel.raw.setPlainText(json.dumps(draft))
        self.assertEqual(self.panel.configuration(), draft)

    def test_pyrowave_foveation_preset_and_override_round_trip(self):
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('pyrowave-444-experimental'))
        self.panel.load_preset.click()
        self.assertEqual(self.panel.pyrowave_foveation.value(), -1)
        self.assertIsNone(self.panel.configuration().get('pyrowave-foveation'))
        self.panel.pyrowave_foveation.setValue(30)
        self.panel.save()
        value = self.server.calls[-1][1]
        self.assertEqual(value['pyrowave-foveation'], 30)
        self.assertEqual(value['pyrowave-bitrate'], 400_000_000)
        self.assertEqual(value['application'], self.config['application'])
        self.panel.dirty = False
        self.server.state.emit({'JsonConfiguration': json.dumps(value)})
        self.assertEqual(self.panel.pyrowave_foveation.value(), 30)
        self.panel.pyrowave_foveation.setValue(-1)
        self.assertNotIn('pyrowave-foveation', self.panel.configuration())

    def test_pyrowave_preset_restores_client_foveation_from_host_override(self):
        config = {**self.config, 'pyrowave-foveation': 0}
        self.server.state.emit({'JsonConfiguration': json.dumps(config)})
        self.assertEqual(self.panel.pyrowave_foveation.value(), 0)
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('pyrowave-444-experimental'))
        self.panel.load_preset.click()
        self.assertEqual(self.panel.pyrowave_foveation.value(), -1)
        value = self.panel.configuration()
        self.assertIsNone(value.get('pyrowave-foveation'))
        self.assertEqual(value['scale'], config['scale'])
        self.panel.save()
        self.assertIsNone(self.server.calls[-1][1].get('pyrowave-foveation'))

    def test_noop_is_lossless(self): self.assertEqual(self.panel.configuration(), self.config)
    def test_preset_load_preserves_draft_and_only_saves_explicitly(self):
        self.panel.hostname.setText('draft-host')
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('hevc-balanced-100'))
        self.panel.load_preset.click()
        value = self.panel.configuration()
        self.assertEqual(value['hostname'], 'draft-host')
        self.assertEqual(value['unknown-future'], self.config['unknown-future'])
        self.assertEqual(value['application'], self.config['application'])
        self.assertEqual(value['encoder']['encoder'], 'qsv')
        self.assertEqual(value['encoder']['options']['gpb'], '0')
        self.assertEqual(value['bit-depth'], 8)
        self.assertFalse(value['tcp-only'])
        self.assertEqual(self.server.calls, [])
        self.assertIn('100 Mbit/s', self.panel.preset_notice.text())
        self.server.state.emit({'JsonConfiguration':json.dumps(self.config)})
        self.assertEqual(self.panel.configuration(), value)
        self.panel.save()
        self.assertEqual(self.server.calls[-1], ('save', value, self.config))
    def test_cqp_preset_custom_quantizer_and_return_to_cbr(self):
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('hevc-cqp-24-experimental'))
        self.assertTrue(self.panel.preset_qp.isEnabled())
        self.panel.preset_qp.setValue(30)
        self.panel.load_preset.click()
        options = self.panel.configuration()['encoder']['options']
        self.assertEqual(options['rate_control'], 'cqp')
        self.assertEqual(options['qp'], '30')
        self.assertEqual(options['low_delay_brc'], '0')
        self.assertIn('uncapped', self.panel.preset_notice.text())
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('hevc-balanced-100'))
        self.panel.load_preset.click()
        self.assertFalse(self.panel.preset_qp.isEnabled())
        self.assertNotIn('rate_control', self.panel.configuration()['encoder']['options'])
        self.assertNotIn('qp', self.panel.configuration()['encoder']['options'])
    def test_preset_does_not_discard_invalid_json(self):
        self.panel.raw.setPlainText('{broken')
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('hevc-balanced-100'))
        self.panel.load_preset.click()
        self.assertEqual(self.panel.raw.toPlainText(), '{broken')
        self.assertEqual(self.server.calls, [])
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
    def test_status_poll_preserves_json_scroll_and_selection(self):
        config = {**self.config, 'long-list': list(range(200))}
        state = {'JsonConfiguration': json.dumps(config)}
        self.server.state.emit(state)
        self.panel.resize(900, 1000); self.panel.show(); self.app.processEvents()
        cursor = self.panel.raw.textCursor()
        cursor.setPosition(300); cursor.setPosition(340, QTextCursor.MoveMode.KeepAnchor)
        self.panel.raw.setTextCursor(cursor)
        bar = self.panel.raw.verticalScrollBar()
        bar.setValue(bar.maximum() // 2)
        before = bar.value()
        self.assertGreater(before, 0)
        changes = []
        self.panel.raw.textChanged.connect(lambda: changes.append(True))
        for _ in range(5): self.server.state.emit(state)
        self.assertEqual(changes, [])
        self.assertEqual(bar.value(), before)
        self.assertEqual(self.panel.raw.textCursor().position(), 340)
        self.assertEqual(self.panel.raw.textCursor().anchor(), 300)
        # Real external config updates still refresh, keeping the reading position.
        config['external-option'] = True
        self.server.state.emit({'JsonConfiguration': json.dumps(config)})
        self.assertTrue(self.panel.configuration()['external-option'])
        self.assertEqual(bar.value(), before)
        self.assertEqual(self.panel.raw.textCursor().position(), 340)
        self.assertEqual(self.panel.raw.textCursor().anchor(), 300)
    def test_small_window_scrolls_instead_of_clipping_encoder_controls(self):
        with tempfile.TemporaryDirectory() as directory:
            window = Dashboard(self.server, Path(directory)/'shora.toml', Path(directory)/'control', attach=False)
            window.poll.stop(); window.resize(900, 720); window.show(); self.app.processEvents()
            try:
                scroll = window.tabs.widget(0)
                self.assertIsInstance(scroll, QScrollArea)
                panel = scroll.widget()
                panel.preset.setCurrentIndex(panel.preset.findData('hevc-cqp-24-experimental'))
                self.app.processEvents()
                self.assertGreater(scroll.verticalScrollBar().maximum(), 0)
                for combo in (panel.preset, panel.encoder, panel.codec, panel.depth):
                    option = QStyleOptionComboBox(); combo.initStyleOption(option)
                    rect = combo.style().subControlRect(QStyle.ComplexControl.CC_ComboBox, option,
                                                       QStyle.SubControl.SC_ComboBoxEditField, combo)
                    self.assertGreaterEqual(rect.height(), combo.fontMetrics().height())
                    self.assertGreaterEqual(rect.width(), combo.fontMetrics().horizontalAdvance(combo.currentText()))
                window.grab().save('/tmp/wivrn-streaming-layout-fixed.png')
            finally:
                window.close()
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

    def test_reference_health_blocks_reset_and_cancels_countdown(self):
        from tracking_panel import TrackingPanel
        panel = TrackingPanel()
        snapshot = {"trackers":[{"active":True}], "hmd_pose_received":True,
                    "spatial_reference":{"ready":False, "state":"reference_settling", "anchors":3},
                    "reference_needs_reset":True}
        panel.set_snapshot(snapshot)
        self.assertFalse(panel.calibrate_button.isEnabled())
        self.assertIn("Automatic correction paused", panel.calibration_status.text())
        snapshot["spatial_reference"]["ready"] = True
        panel.set_snapshot(snapshot)
        self.assertTrue(panel.calibrate_button.isEnabled())
        panel.start_calibration()
        self.assertIsNotNone(panel.deadline)
        snapshot["spatial_reference"]["ready"] = False
        panel.set_snapshot(snapshot)
        self.assertIsNone(panel.deadline)
        self.assertFalse(panel.timer.isActive())
        snapshot.pop("spatial_reference")
        panel.set_snapshot(snapshot)
        self.assertTrue(panel.calibrate_button.isEnabled()) # Standalone compatibility.
        panel.close()

    def test_apply_button_sends_settings_and_reports_profile_persistence(self):
        from tracking_panel import TrackingPanel, DEFAULTS
        panel = TrackingPanel()
        commands = []
        panel.command_requested.connect(commands.append)
        snapshot = {'settings': dict(DEFAULTS), 'config_path': '/tmp/intended.toml'}
        panel.set_snapshot(snapshot)
        panel.height.setValue(173)
        panel.apply_button.click()
        self.assertEqual(commands[-1]['command'], 'set_settings')
        self.assertEqual(commands[-1]['settings']['height_m'], 1.73)
        panel.command_finished('set_settings', True, '')
        self.assertIn('saved to the startup profile', panel.notice.text())
        panel.command_finished('save_relaxed_pose', True, '')
        self.assertIn('saved to the startup profile', panel.calibration_status.text())
        panel.command_finished('set_settings', False, 'Cannot save TOML')
        self.assertEqual(panel.notice.text(), 'Cannot save TOML')
        panel.close()

    def test_walking_tuning_roundtrips_through_apply_and_export(self):
        from tracking_panel import TrackingPanel, DEFAULTS
        panel = TrackingPanel()
        commands = []
        panel.command_requested.connect(commands.append)
        walking = dict(lift_deg=12.0, sensitivity=1.0, max_stick=.8, stop_ms=350,
                       turn_deadzone_deg=8.0, turn_range_deg=35.0, max_turn=.65)
        snapshot = {"settings": dict(DEFAULTS, walking=walking), "walking_status": "Walking ready"}
        panel.set_snapshot(snapshot)
        panel.walk_fields["sensitivity"].setValue(1.4)
        panel.walk_fields["stop_ms"].setValue(250)
        panel.apply_button.click()
        sent = commands[-1]["settings"]["walking"]
        self.assertEqual(sent["sensitivity"], 1.4)
        self.assertEqual(sent["stop_ms"], 250)
        self.assertIsInstance(sent["stop_ms"], int)
        snapshot["settings"]["walking"] = sent
        panel.command_finished("set_settings", True, "")
        panel.set_snapshot(snapshot)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "walking.toml"
            panel.export_profile(path)
            self.assertEqual(tomllib.loads(path.read_text())["walking"], sent)
        self.assertEqual(panel.walk_status.text(), "Walking ready")
        panel.close()

    def test_worker_profile_updates_refresh_clean_editor_and_preserve_drafts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'shora.toml'
            path.write_text('height_m=1.8\n')
            window = Dashboard(self.server, path, Path(directory)/'control', attach=False)
            window.poll.stop()
            path.write_text('height_m=1.7\n')
            window.update_snapshot({})
            self.assertEqual(window.profile.toPlainText(), 'height_m=1.7\n')
            self.assertEqual(window.profile_original, 'height_m=1.7\n')
            window.profile.setPlainText('height_m=1.9\n')
            path.write_text('height_m=1.6\n')
            window.update_snapshot({})
            self.assertEqual(window.profile.toPlainText(), 'height_m=1.9\n')
            window.save_profile()
            self.assertEqual(path.read_text(), 'height_m=1.6\n')
            window.load_profile()
            window.haritorax.setChecked(True)
            path.write_text('height_m=1.5\n')
            window.update_snapshot({})
            self.assertTrue(window.haritorax.isChecked())
            self.assertEqual(window.profile_original, 'height_m=1.6\n')
            window.close()

    def test_pose_capture_result_survives_status_polling(self):
        from tracking_panel import TrackingPanel, DEFAULTS
        panel = TrackingPanel()
        snapshot = {'settings': dict(DEFAULTS), 'trackers': [{'active': True}]}
        panel.set_snapshot(snapshot)
        error = 'Cannot save relaxed pose: the room or headset reference changed'
        panel.command_finished('save_relaxed_pose', False, error)
        for _ in range(5):
            panel.set_snapshot(snapshot)
        self.assertIn(error, panel.calibration_status.text())
        panel.start_calibration()
        self.assertEqual(panel.calibration_feedback, '')
        panel.cancel_calibration()
        panel.command_finished('save_relaxed_pose', True, '')
        panel.set_snapshot(snapshot)
        self.assertIn('Relaxed pose saved', panel.calibration_status.text())
        panel.close()

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
