#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import types
import unittest
from unittest import mock
import zlib

import codec
import fixtures

HERE = Path(__file__).resolve().parent
RUN_PAIR = HERE.parent / "research_workflow" / "run_pair.py"


class CodecTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.dataset = fixtures.create(self.root / "data")
        self.config_path = self.dataset.parent / "config.json"
        self.config, self.config_hash, _ = codec.load_config(self.config_path)
        self.model = codec.DenseAutoencoder.initialize(7)
        document = {"schema": 1, "architecture": codec.ARCH, "quality_arm": "new_lossy",
                    "config_sha256": self.config_hash, "state": self.model.state,
                    "training": {"fixture": True}}
        self.model_path = self.root / "model.json"
        codec.write_new(self.model_path, codec.canonical(document))
        self.model_hash = codec.digest(self.model_path.read_bytes())
        self.source = self.dataset.parent / "test-left.rgb"
        self.raw = self.source.read_bytes()
        self.stream = codec.encode(self.raw, 17, 9, "left", self.model, self.model_hash,
                                   self.config, self.config_hash)

    def decode(self, stream, **changes):
        args = dict(model=self.model, model_hash=self.model_hash, config=self.config,
                    config_hash=self.config_hash, width=17, height=9, eye="left")
        args.update(changes)
        return codec.decode(stream, **args)

    def argv(self, operation, source, target, eye="left"):
        return [sys.executable, str(HERE / "codec.py"), operation,
                "--input", str(source), "--output", str(target),
                "--model", str(self.model_path), "--config", str(self.config_path),
                "--width", "17", "--height", "9", "--eye", eye]

    def run_adapter(self, argv, expected=0):
        result = subprocess.run(argv, cwd=self.root, capture_output=True, timeout=30)
        self.assertEqual(result.returncode, expected, result.stderr.decode())
        return result

    def test_actual_bitstream_and_odd_tail(self):
        decoded = self.decode(self.stream)
        self.assertEqual(len(decoded), len(self.raw))
        self.assertEqual(len(self.stream), codec.HEADER.size + 5 * 3 * 2)
        self.assertNotEqual(decoded, self.raw)  # explicitly lossy fixture
        self.assertEqual(decoded[:3], decoded[3:6])  # one reconstructed block color
        for w, h in ((1, 1), (2, 7), (9, 3)):
            raw = bytes((i * 13) % 256 for i in range(w * h * 3))
            encoded = codec.encode(raw, w, h, "right", self.model, self.model_hash,
                                   self.config, self.config_hash)
            self.assertEqual(len(self.decode(encoded, width=w, height=h, eye="right")), len(raw))

    def test_learning_reduces_fixed_target_error(self):
        target = [0.1, 0.25, 0.4]
        def loss():
            reconstruction = self.model.decode_latent(self.model.encode_latent(target))
            return sum((a / 255.0 - b) ** 2 for a, b in zip(reconstruction, target))
        before = loss()
        for _ in range(100):
            self.model.train_step(target, 0.3)
        self.assertLess(loss(), before * 0.5)

    def test_decoder_has_no_original_or_encoder_state(self):
        encoded, decoded = self.root / "one.nic", self.root / "one.rgb"
        self.run_adapter(self.argv("encode", self.source, encoded))
        self.source.unlink()
        self.run_adapter(self.argv("decode", encoded, decoded))
        self.assertEqual(decoded.read_bytes(), self.decode(encoded.read_bytes()))

    def test_output_is_exclusive(self):
        output = self.root / "existing.rgb"
        output.write_bytes(b"preserve")
        encoded = self.root / "one.nic"
        encoded.write_bytes(self.stream)
        self.run_adapter(self.argv("decode", encoded, output), expected=2)
        self.assertEqual(output.read_bytes(), b"preserve")

    def test_truncation_trailing_and_payload_corruption(self):
        corrupt = bytearray(self.stream)
        corrupt[-1] ^= 1
        for raw in (b"", self.stream[:10], self.stream[:-1], self.stream + b"x", bytes(corrupt)):
            with self.subTest(size=len(raw)), self.assertRaises(ValueError):
                self.decode(raw)

    def test_semantic_header_mutations_with_repaired_checksum(self):
        original = list(codec.HEADER.unpack_from(self.stream))
        for index, value in ((0, b"BAD!"), (1, 2), (2, 1), (3, 0), (4, 1),
                             (5, 0), (6, 0), (7, 4096), (8, 3), (9, 3),
                             (10, 2**32 - 1), (11, bytes(32)), (12, bytes(32))):
            fields = list(original)
            fields[index] = value
            header = codec.HEADER.pack(*fields)
            payload = self.stream[codec.HEADER.size:]
            changed = header[:-4] + struct.pack("<I", zlib.crc32(header[:-4] + payload)) + payload
            with self.subTest(index=index), self.assertRaises(ValueError):
                self.decode(changed)

    def test_dimension_eye_model_config_mismatch(self):
        for changes in ({"width": 18}, {"eye": "right"}, {"model_hash": "0" * 64},
                        {"config_hash": "1" * 64}):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                self.decode(self.stream, **changes)
        with self.assertRaises(ValueError):
            codec.load_model(self.model_path, "0" * 64)

    def test_weights_are_strict_finite_and_bounded(self):
        for bad in (float("nan"), float("inf"), 101, True):
            document = json.loads(self.model_path.read_bytes())
            document["state"][0]["weight"][0][0] = bad
            self.model_path.write_text(json.dumps(document))
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                codec.load_model(self.model_path, self.config_hash)
        state = codec.DenseAutoencoder.initialize(7).state
        state[0]["weight"][0].pop()
        with self.assertRaises(ValueError):
            codec.DenseAutoencoder(state)
        with self.assertRaises(ValueError):
            codec.parse_json(b'{"x":1,"x":2}')

    def test_encoder_cap_and_raw_length(self):
        with self.assertRaisesRegex(ValueError, "cap"):
            codec.encode(self.raw, 17, 9, "left", self.model, self.model_hash,
                         self.config, self.config_hash, len(self.stream) - 1)
        with self.assertRaisesRegex(ValueError, "byte count"):
            codec.encode(self.raw[:-1], 17, 9, "left", self.model, self.model_hash,
                         self.config, self.config_hash)
        oversized = self.root / "oversized.nic"
        oversized.write_bytes(bytes(codec.MAX_STREAM + 1))
        self.run_adapter(self.argv("decode", oversized, self.root / "bad.rgb"), expected=2)
        self.assertFalse((self.root / "bad.rgb").exists())

    def train_args(self):
        return types.SimpleNamespace(dataset=str(self.dataset), config=str(self.config_path),
                                     epochs=3, max_blocks=32, seed=7, learning_rate=0.1,
                                     output_dir=str(self.root / "trained"))

    def test_training_updates_weights_and_records_actual_quality(self):
        codec.train(self.train_args())
        bundle = self.root / "trained"
        trained = json.loads((bundle / "model.json").read_bytes())
        receipt = json.loads((bundle / "training-receipt.json").read_bytes())
        self.assertNotEqual(trained["state"], self.model.state)
        self.assertEqual(receipt["quality_arm"], "new_lossy")
        self.assertEqual(receipt["model_sha256"], codec.digest((bundle / "model.json").read_bytes()))
        self.assertEqual(trained["training"]["steps"], 90)
        self.assertEqual(set(receipt["metrics"]), {"train", "validation", "test"})
        self.assertTrue(all(v["rgb8_mse"] >= 0 for v in receipt["metrics"].values()))

    def test_all_declared_split_leakage_fails_before_model_creation(self):
        original = self.dataset.read_bytes()
        for key in ("source_id", "scene_id", "session_id", "sha256"):
            data = json.loads(original)
            data["samples"][4][key] = data["samples"][0][key]
            self.dataset.write_bytes(codec.canonical(data))
            with self.subTest(key=key), mock.patch.object(
                    codec.DenseAutoencoder, "initialize", side_effect=AssertionError("training started")):
                with self.assertRaisesRegex(ValueError, "split leakage"):
                    codec.train(self.train_args())
            self.assertFalse((self.root / "trained").exists())

    def test_data_hash_and_stereo_integrity(self):
        self.source.write_bytes(bytes(len(self.raw)))
        with self.assertRaisesRegex(ValueError, "hash mismatch"):
            codec.load_dataset(self.dataset)
        data = json.loads(self.dataset.read_bytes())
        data["samples"][1]["warp_id"] += 1
        self.dataset.write_bytes(codec.canonical(data))
        with self.assertRaisesRegex(ValueError, "incoherent stereo"):
            codec.load_dataset(self.dataset)

    def test_whole_stereo_cap_rejects_both_eyes(self):
        # The external harness uses separate decoder processes and actual file sizes.
        output = self.root / "pair"
        encoder = self.argv("encode", "{input}", "{output}", "{eye}")
        decoder = self.argv("decode", "{input}", "{output}", "{eye}")
        for argv in (encoder, decoder):
            argv[argv.index("--width") + 1] = "{width}"
            argv[argv.index("--height") + 1] = "{height}"
        result = subprocess.run(
            [sys.executable, str(RUN_PAIR), "--left", str(self.source),
             "--right", str(self.dataset.parent / "test-right.rgb"),
             "--width", "17", "--height", "9", "--encoder-argv", json.dumps(encoder),
             "--decoder-argv", json.dumps(decoder), "--output", str(output),
             "--stereo-payload-cap", str(2 * len(self.stream) - 1)],
            cwd=self.root, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 2, result.stderr.decode())
        receipt = json.loads((output / "receipt.json").read_bytes())
        self.assertEqual(receipt["status"], "rejected")
        self.assertEqual(receipt["stereo"]["encoded_payload_bytes"], 2 * len(self.stream))
        self.assertEqual(receipt["stereo"]["admitted_eyes"], [])


if __name__ == "__main__":
    unittest.main()
