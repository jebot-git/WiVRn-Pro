# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import json
import test_dashboard as base
from wivrn_dashboard import encoder_presets, portable_preset
from motoc_panel import MotocPanel
from shora_qt import ControlClient

class PortabilityDashboardTests(unittest.TestCase):
    setUpClass = classmethod(base.DashboardTests.setUpClass.__func__)
    setUp = base.DashboardTests.setUp
    tearDown = base.DashboardTests.tearDown
    def test_motoc_controller_toggle_saves_shared_json_without_touching_calibration(self):
        client = ControlClient()
        motoc = MotocPanel(client, self.panel)
        try:
            self.assertTrue(self.panel.lighthouse_controllers.isChecked())
            self.assertEqual(self.panel.configuration(), self.config)
            self.panel.lighthouse_controllers.setChecked(False)
            motoc.save_controllers.click()
            self.assertEqual(self.server.calls[-1], ('save', {**self.config, 'motoc-lighthouse-controllers': False}, self.config))
            self.assertFalse(motoc.dirty)
            self.server.failure.emit('Concurrent edit; reload before saving')
            self.assertIn('Concurrent edit', motoc.controller_notice.text())
            self.panel.raw.setPlainText('{bad json')
            motoc.save_controllers.click()
            self.assertIn('Expecting', motoc.controller_notice.text())
        finally:
            motoc.close()

    def test_motoc_controller_setting_survives_reload_and_presets(self):
        config = {**self.config, 'motoc-lighthouse-controllers': False}
        self.server.state.emit({'JsonConfiguration': json.dumps(config)})
        self.assertFalse(self.panel.lighthouse_controllers.isChecked())
        self.assertEqual(self.panel.configuration(), config)
        self.panel.preset.setCurrentIndex(self.panel.preset.findData('hevc-cqp-24-experimental'))
        self.panel.load_encoder_preset()
        self.assertFalse(self.panel.configuration()['motoc-lighthouse-controllers'])
        self.panel.lighthouse_controllers.setChecked(True)
        self.assertTrue(self.panel.configuration()['motoc-lighthouse-controllers'])

    def test_exclusive_bridge_and_xrizer_modes_preserve_other_settings(self):
        self.panel.lighthouse.setCurrentIndex(1)
        self.panel.xrizer.setCurrentIndex(1)
        result = self.panel.configuration()
        self.assertEqual(result['tracking-mode'], 'motoc')
        self.assertTrue(result['xrizer-auto-detect'])
        self.assertNotIn('openvr-compat-path', result)
        self.assertEqual(result['encoder'], self.config['encoder'])
        self.panel.xrizer.setCurrentIndex(2); self.panel.xrizer_path.setText('/custom/xrizer')
        result = self.panel.configuration()
        self.assertEqual(result['openvr-compat-path'], '/custom/xrizer')
        self.assertNotIn('xrizer-auto-detect', result)
    def test_presets_map_cqp_without_qsv_private_options(self):
        presets = encoder_presets()
        self.assertGreaterEqual(len(presets), 8)
        for name, preset in presets.items():
            for backend in ['qsv', 'nvenc', 'vaapi']:
                host = portable_preset(preset, backend)
                self.assertEqual(host['encoder']['encoder'], backend)
                if backend != 'qsv':
                    self.assertFalse(set(host['encoder']['options']) - {'rate_control', 'qp'})
                if preset['requires_cqp_branch']:
                    self.assertEqual(host['encoder']['options']['rate_control'], 'cqp')
                self.assertEqual(preset['host_config']['encoder']['encoder'], 'qsv')
    def test_loading_portable_profile_keeps_mode_and_manual_configuration(self):
        self.panel.lighthouse.setCurrentIndex(1)
        self.panel.preset_backend.setCurrentText('nvenc')
        index = self.panel.preset.findData('hevc-cqp-24-experimental')
        self.assertGreater(index, 0)
        self.panel.preset.setCurrentIndex(index)
        self.panel.preset_qp.setValue(20)
        self.panel.load_encoder_preset()
        value = self.panel.configuration()
        self.assertEqual(value['encoder']['options'], {'rate_control': 'cqp', 'qp': '20'})
        self.assertEqual(value['tracking-mode'], 'motoc')
        self.assertEqual(value['application'], self.config['application'])
