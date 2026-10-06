import json, sys, tempfile, time, unittest
from pathlib import Path
from dataclasses import replace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from settings import Settings
from translation import detect, Translator
from audio import Segmenter, FRAME_BYTES, direction
from subtitle_recording import Recorder


class LocalOnlyTests(unittest.TestCase):
    def test_cloud_configuration_rejected(self):
        for field in [
            "api_key",
            "provider",
            "local_translation",
            "recognition_backend",
            "model",
            "endpoint",
        ]:
            with self.assertRaises(ValueError):
                Settings.parse({field: "external"})

    def test_bad_models_and_limits(self):
        for d in [
            {"local_translation_model": "repo/id"},
            {"duration": float("nan")},
            {"audio_sink": "--remote=host"},
            {"enabled": "yes"},
            {"ocr_max_regions": 2.5},
        ]:
            with self.assertRaises(ValueError):
                Settings.parse(d)

    def test_script_detection(self):
        for text, language in [
            ("日本語です", "Japanese"),
            ("한국어", "Korean"),
            ("中文", "Chinese"),
            ("สวัสดี", "Thai"),
            ("Hello", "English"),
        ]:
            self.assertEqual(detect(text), language)

    def test_local_translation_original_never_loads_or_calls_model(self):
        t = Translator.__new__(Translator)
        self.assertEqual(t.translate("hello", "Original"), "hello")

    def test_no_provider_import_or_exec_hooks(self):
        root = Path(__file__).resolve().parents[1] / "python"
        forbidden = [
            "api.openai.com",
            "deepseek-flash",
            "codex",
            "OPENAI_API_KEY",
            "urlopen",
            "urllib.request",
            "requests.post",
        ]
        for path in root.glob("*.py"):
            text = path.read_text()
            for word in forbidden:
                self.assertNotIn(word, text, str(path))

    def test_audio_gate_and_direction(self):
        gate = Segmenter(Settings())
        self.assertIsNone(gate.feed(bytes(FRAME_BYTES), time.monotonic()))
        bearing, spatial = direction([0.7] * 5, True)
        self.assertTrue(spatial)
        self.assertGreater(bearing, 0)

    def test_recording_is_opt_in(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "recordings"
            r = Recorder(root)
            r.configure(Settings())
            self.assertIsNone(
                r.record("speech", [{"text": "private"}], time.monotonic(), bytes(320))
            )
            r.close()
            self.assertFalse(root.exists())

    def test_recording_local_transcript(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp) / "recordings"
            r = Recorder(root)
            r.configure(replace(Settings(), log_speech=True))
            f = r.record(
                "speech",
                [{"text": "hello", "source_text": "こんにちは"}],
                time.monotonic(),
                bytes(320),
            )
            f.result()
            r.close()
            files = list(root.rglob("*.jsonl"))
            self.assertEqual(len(files), 1)
            self.assertNotEqual(files[0].stat().st_mode & 0o077, 0o077)
            self.assertEqual(
                json.loads(files[0].read_text())["recognition_backend"], "openvino"
            )


if __name__ == "__main__":
    unittest.main()
