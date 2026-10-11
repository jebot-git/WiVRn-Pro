# SPDX-License-Identifier: GPL-3.0-or-later
import unittest
import test_dashboard as base
from wivrn_dashboard import encoder_presets, portable_preset

class PortabilityDashboardTests(unittest.TestCase):
    setUpClass = classmethod(base.DashboardTests.setUpClass.__func__)
    setUp = base.DashboardTests.setUp
    tearDown = base.DashboardTests.tearDown
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
