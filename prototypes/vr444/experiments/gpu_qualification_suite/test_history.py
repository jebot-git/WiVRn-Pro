import json
import unittest
from history import bindings, digest, safe_path, verify_receipt


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.path = 'prototypes/vr444/experiments/example/probe.cpp'
        self.content = b'int main() { return 0; }\n'
        self.files = {self.path: self.content}
        self.receipt = {'source_sha256': {self.path: digest(self.content)}}

    def check(self, receipt=None, files=None):
        return verify_receipt(json.dumps(self.receipt if receipt is None else receipt).encode(),
                              (self.files if files is None else files).get)

    def test_historical_blob_not_current_worktree(self):
        result = self.check()
        self.assertEqual(result['verified_source_count'], 1)
        self.assertEqual(result['verified_tracked_bindings'], self.receipt['source_sha256'])
        self.assertEqual(result['external_artifact_hashes_not_reverified'], {})

    def test_changed_and_missing_source_fail(self):
        for files in ({}, {self.path: b'changed'}):
            with self.subTest(files=files), self.assertRaises(ValueError):
                self.check(files=files)

    def test_external_artifact_is_explicitly_unverified(self):
        self.receipt['binaries'] = [{'path': '/tmp/retired/probe', 'sha256': '1' * 64}]
        result = self.check()
        self.assertEqual(result['external_artifact_hashes_not_reverified'], {'/tmp/retired/probe': '1' * 64})
        self.assertNotIn('/tmp/retired/probe', result['verified_tracked_bindings'])

    def test_omitted_source_and_conflicting_hash_fail(self):
        for receipt in ({'headset_used': False},
                        {'sources': [{'path': self.path, 'sha256': digest(self.content)},
                                     {'path': self.path, 'sha256': '0' * 64}]}):
            with self.subTest(receipt=receipt), self.assertRaises(ValueError):
                self.check(receipt)

    def test_malformed_hash_and_unsafe_source_fail(self):
        for path, sha in ((self.path, 'bad'), ('../probe.cpp', digest(self.content)),
                          ('/tmp/probe.cpp', digest(self.content)), (self.path, None)):
            with self.subTest(path=path, sha=sha), self.assertRaises(ValueError):
                self.check({'sources': [{'path': path, 'sha256': sha}]})
        for value in (None, 12, {}, []):
            with self.subTest(map_value=value), self.assertRaises(ValueError):
                self.check({'source_sha256': {self.path: value}})

    def test_catalog_cannot_disable_source_binding(self):
        for minimum in (0, -1, True, None, 1.0):
            with self.subTest(minimum=minimum), self.assertRaises(ValueError):
                verify_receipt(json.dumps(self.receipt).encode(), self.files.get, minimum)

    def test_both_receipt_forms_and_path_boundaries(self):
        sha = digest(self.content)
        self.assertEqual(bindings({'sources': [{'path': self.path, 'sha256': sha}],
                                   'source_sha256': {self.path: sha}}), {self.path: sha})
        for path in ('', '../x', '/abs/x', 'x//y', 'x/./y', 'x\0y'):
            with self.subTest(path=path):
                self.assertFalse(safe_path(path))
        self.assertTrue(safe_path(self.path))


if __name__ == '__main__':
    unittest.main()
