"""Validate admission/provenance evidence rather than compression byte arithmetic alone."""
import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('compression_live', ROOT/'integration/live.py')
live = importlib.util.module_from_spec(spec)
spec.loader.exec_module(live)


class CompressionEvidence(unittest.TestCase):
    def rows(self, *, compressed=True, requested=True, raw=1024, wire=512):
        encoded = {(7, 0): dict(compression_requested=requested,
                                compression_attempted=requested,
                                compression_applied=compressed,
                                uncompressed_payload_bytes=raw,
                                wire_payload_bytes=wire,
                                payload_bytes=wire+48,
                                compression_ms=.08)}
        decoded = {(7, 0): dict(compression_applied=compressed,
                                decoded_payload_bytes=raw,
                                bytes=wire+48,
                                decompression_ms=.02)}
        return encoded, decoded

    def assert_bad(self, encoded, decoded, requested='lz4'):
        result = live.compression_metrics(encoded, decoded, requested)
        self.assertNotIn(result['status'], ('active', 'off', 'raw_only'))
        self.assertTrue(result['errors'])

    def test_mixed_compressed_and_raw_frames_account_exact_payload_bytes(self):
        encoded, decoded = self.rows()
        raw_e, raw_d = self.rows(compressed=False, raw=250, wire=250)
        encoded[(8, 1)] = raw_e[(7, 0)]
        decoded[(8, 1)] = raw_d[(7, 0)]
        result = live.compression_metrics(encoded, decoded, 'lz4')
        self.assertEqual(result['status'], 'active')
        self.assertEqual(result['compressed_eye_frames'], 1)
        self.assertEqual(result['raw_eye_frames'], 1)
        self.assertEqual(result['original_payload_bytes'], 1274)
        self.assertEqual(result['wire_payload_bytes'], 762)
        self.assertEqual(result['saved_bytes'], 512)
        self.assertAlmostEqual(result['wire_ratio'], 762/1274)
        self.assertEqual(result['encode_ms']['count'], 2)
        self.assertEqual(result['decode_ms']['p50'], .02)

    def test_off_and_opted_in_raw_escape_are_distinct(self):
        for requested, status in [(False, 'off'), (True, 'raw_only')]:
            with self.subTest(requested=requested):
                encoded, decoded = self.rows(compressed=False, requested=requested, wire=1024)
                result = live.compression_metrics(encoded, decoded, 'lz4' if requested else 'none')
                self.assertEqual(result['status'], status)
                self.assertEqual(result['saved_bytes'], 0)

    def test_missing_fresh_evidence_is_unverified(self):
        self.assertEqual(live.compression_metrics({}, {}, 'lz4')['status'], 'unverified')
        encoded, decoded = self.rows()
        del encoded[(7, 0)]['wire_payload_bytes']
        self.assert_bad(encoded, decoded)

    def test_sender_configuration_must_match_requested_mode(self):
        encoded, decoded = self.rows()
        self.assert_bad(encoded, decoded, 'none')
        encoded[(7, 0)]['compression_requested'] = False
        self.assert_bad(encoded, decoded)

    def test_admission_needs_64_bytes_saved_and_raw_escape_cannot_grow(self):
        encoded, decoded = self.rows(raw=1024, wire=960)
        self.assertEqual(live.compression_metrics(encoded, decoded, 'lz4')['status'], 'active')
        for compressed, wire in [(True, 961), (True, 1024), (False, 1025), (False, 1000)]:
            with self.subTest(compressed=compressed, wire=wire):
                encoded, decoded = self.rows(compressed=compressed, wire=wire)
                self.assert_bad(encoded, decoded)

    def test_payload_sizes_are_bounded_positive_integers(self):
        for raw, wire in [(0, 0), (-1, -1), (2**24+1, 512), (1024, 0), (1024.0, 512), (True, True)]:
            with self.subTest(raw=raw, wire=wire):
                encoded, decoded = self.rows(compressed=False, raw=raw, wire=wire)
                self.assert_bad(encoded, decoded)

    def test_sender_and_receiver_envelope_lengths_are_both_checked(self):
        for side, field in [('sender', 'payload_bytes'), ('receiver', 'bytes')]:
            with self.subTest(side=side):
                encoded, decoded = self.rows()
                target = encoded if side == 'sender' else decoded
                target[(7, 0)][field] += 1
                self.assert_bad(encoded, decoded)
                del target[(7, 0)][field]
                self.assert_bad(encoded, decoded)

    def test_receiver_decompressed_size_and_flag_match_sender(self):
        for field, value in [('decoded_payload_bytes', 1023), ('compression_applied', False)]:
            with self.subTest(field=field):
                encoded, decoded = self.rows()
                decoded[(7, 0)][field] = value
                self.assert_bad(encoded, decoded)

    def test_receiver_requires_matching_frame_and_eye_provenance(self):
        encoded, decoded = self.rows()
        decoded[(7, 1)] = decoded.pop((7, 0))
        self.assert_bad(encoded, decoded)
        decoded[(8, 0)] = decoded.pop((7, 1))
        self.assert_bad(encoded, decoded)

    def test_flags_must_be_boolean_and_applied_requires_attempt(self):
        for field in ('compression_requested', 'compression_attempted', 'compression_applied'):
            with self.subTest(field=field):
                encoded, decoded = self.rows()
                encoded[(7, 0)][field] = 1
                self.assert_bad(encoded, decoded)
        encoded, decoded = self.rows()
        encoded[(7, 0)]['compression_attempted'] = False
        self.assert_bad(encoded, decoded)
        encoded, decoded = self.rows()
        decoded[(7, 0)]['compression_applied'] = 1
        self.assert_bad(encoded, decoded)

    def test_config_rejects_non_vr444_before_side_effects(self):
        from types import SimpleNamespace
        for mode in ('hevc-carrier', 'raw-rgba'):
            args = SimpleNamespace(frame_stride=12, unattended=False, compression='lz4', mode=mode)
            with self.subTest(mode=mode), patch.object(live, 'command') as command:
                with self.assertRaisesRegex(ValueError, 'VR444|vr444'):
                    live.run(args)
                command.assert_not_called()

    def test_suite_forwards_compression_and_defaults_off(self):
        sys.path.insert(0, str(ROOT))
        try:
            import suite
            for extra, expected in [([], 'none'), (['--compression', 'lz4'], 'lz4')]:
                argv = ['suite.py', 'live-integration', '--source', '/unused', '--serial', 'test',
                        '--out', '/unused-output', '--mode', 'vulkan-vr444'] + extra
                with self.subTest(expected=expected), patch.object(sys, 'argv', argv), \
                     patch('integration.live.run', return_value={'status': 'pass'}) as run, \
                     contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(suite.main(), 0)
                self.assertEqual(run.call_args.args[0].compression, expected)
        finally:
            sys.path.pop(0)


if __name__ == '__main__':
    unittest.main()
