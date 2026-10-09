import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import native_campaign as campaign


class CampaignAdmission(unittest.TestCase):
    def fixture(self, root):
        native = root/'native'
        native.mkdir()
        (native/'server').write_bytes(b'bound server')
        external = root/'comp_swapchain.c'
        external.write_bytes(b'bound external source')
        receipt = {'sha256': {'server': campaign.sha(native/'server')},
                   'external_artifacts_sha256': {str(external): campaign.sha(external)}}
        path = root/'receipt.json'
        path.write_text(json.dumps(receipt))
        return {'source': str(native), 'build_receipt': str(path),
                'build_receipt_sha256': campaign.sha(path)}, receipt, external

    def test_native_and_external_inputs_are_both_checked(self):
        for changed in ('server', 'external'):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as temp:
                plan, _, external = self.fixture(Path(temp))
                self.assertEqual(campaign.verify_build(plan), 2)
                target = external if changed == 'external' else Path(plan['source'])/'server'
                target.write_bytes(b'changed after build')
                with self.assertRaisesRegex(RuntimeError, 'build drift'):
                    campaign.verify_build(plan)

    def test_repaired_receipt_hash_cannot_admit_relative_external_path(self):
        with tempfile.TemporaryDirectory() as temp:
            plan, receipt, _ = self.fixture(Path(temp))
            receipt['external_artifacts_sha256'] = {'comp_swapchain.c': '0'*64}
            Path(plan['build_receipt']).write_text(json.dumps(receipt))
            plan['build_receipt_sha256'] = campaign.sha(plan['build_receipt'])
            with self.assertRaisesRegex(RuntimeError, 'external build artifact inventory'):
                campaign.verify_build(plan)

    def test_changed_receipt_rejected_before_contents_are_trusted(self):
        with tempfile.TemporaryDirectory() as temp:
            plan, receipt, _ = self.fixture(Path(temp))
            receipt['external_artifacts_sha256'] = {}
            Path(plan['build_receipt']).write_text(json.dumps(receipt))
            with self.assertRaisesRegex(RuntimeError, 'build receipt changed'):
                campaign.verify_build(plan)

    def test_missing_wear_readiness_stops_before_output_or_commands(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp); plan = root/'plan.json'; out = root/'out'
            plan.write_text(json.dumps({'headset_readiness_required': True,
                                        'headset_readiness_confirmed': False}))
            with patch('sys.argv', ['native_campaign.py', str(plan), '--out', str(out)]), \
                    patch.object(campaign.subprocess, 'run') as run:
                with self.assertRaisesRegex(ValueError, 'worn-headset readiness'):
                    campaign.main()
                run.assert_not_called()
            self.assertFalse(out.exists())


if __name__ == '__main__':
    unittest.main()
