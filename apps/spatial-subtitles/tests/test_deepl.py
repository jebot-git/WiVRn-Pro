import json, sys, tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
import deepl_translation as api
from settings import Settings


class DeepLTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "key.json"
        p = patch.object(api, "KEY_FILE", self.path)
        p.start()
        self.addCleanup(p.stop)
        api.save_key("test-only-not-a-credential:fx")
        self.client = api.DeepLTranslator()
        p = patch.object(api.http.client, "HTTPSConnection")
        self.conn = p.start()
        self.addCleanup(p.stop)
        self.response = self.conn.return_value.getresponse.return_value
        self.response.status = 200

    def respond(self, texts):
        self.response.read.return_value = json.dumps(
            {"translations": [{"text": t} for t in texts]}
        ).encode()

    def test_free_endpoint_auth_batch_autodetect_and_cache(self):
        self.respond(["Hello", "World"])
        result = self.client.translate_many(
            ["こんにちは", "世界", "こんにちは"], "English"
        )
        self.assertEqual(result, ["Hello", "World", "Hello"])
        self.conn.assert_called_once_with("api-free.deepl.com", timeout=4)
        args, kw = self.conn.return_value.request.call_args
        self.assertEqual(args, ("POST", "/v2/translate"))
        self.assertEqual(
            kw["headers"]["Authorization"],
            "DeepL-Auth-Key test-only-not-a-credential:fx",
        )
        body = json.loads(kw["body"])
        self.assertEqual(body["text"], ["こんにちは", "世界"])
        self.assertNotIn("source_lang", body)
        self.assertEqual(body["target_lang"], "EN")
        self.assertEqual(self.client.translate("こんにちは", "English"), "Hello")
        self.conn.assert_called_once()

    def test_serbian_and_explicit_source(self):
        self.respond(["Здраво"])
        self.assertEqual(self.client.translate("Hello", "Serbian", "English"), "Здраво")
        body = json.loads(self.conn.return_value.request.call_args.kwargs["body"])
        self.assertEqual((body["target_lang"], body["source_lang"]), ("SR", "EN"))

    def test_original_and_empty_do_not_use_network(self):
        self.assertEqual(self.client.translate("こんにちは", "Original"), "こんにちは")
        self.assertEqual(self.client.translate_many([], "English"), [])
        self.conn.assert_not_called()

    def test_quota_auth_rate_and_redirect_never_retry_or_expose_response(self):
        for status in (403, 429, 456, 302):
            self.client = api.DeepLTranslator()
            self.conn.reset_mock()
            self.response.status = status
            with self.assertRaises(RuntimeError) as caught:
                self.client.translate("secret text", "English")
            self.assertNotIn("secret text", str(caught.exception))
            self.assertNotIn("test-only", str(caught.exception))
            with self.assertRaises(RuntimeError):
                self.client.translate("another", "English")
            self.conn.assert_called_once()
            self.response.read.assert_not_called()

    def test_invalid_response_and_timeout_are_redacted(self):
        self.response.read.return_value = b"not json"
        with self.assertRaisesRegex(RuntimeError, "invalid response"):
            self.client.translate("x", "English")
        self.client = api.DeepLTranslator()
        self.conn.return_value.request.side_effect = TimeoutError("sensitive headers")
        with self.assertRaises(RuntimeError) as caught:
            self.client.translate("x", "English")
        self.assertNotIn("sensitive", str(caught.exception))

    def test_cancelled_pipeline_never_sends(self):
        self.client.cancelled = Event()
        self.client.cancelled.set()
        with self.assertRaisesRegex(RuntimeError, "settings changed"):
            self.client.translate("hello", "Serbian")
        self.conn.assert_not_called()

    def test_private_key_and_settings_separation(self):
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.path.chmod(0o644)
        with self.assertRaises(ValueError):
            api.read_key()
        self.assertEqual(Settings.parse({}).translation_provider, "Local OpenVINO")
        self.assertEqual(
            Settings.parse(
                {"translation_provider": "DeepL API Free"}
            ).translation_provider,
            "DeepL API Free",
        )
        with self.assertRaises(ValueError):
            Settings.parse({"deepl_api_key": "key"})

    def test_pipeline_provider_avoids_local_translation_model(self):
        from backend import Pipeline

        p = Pipeline.__new__(Pipeline)
        p.cancelled = Event()
        p.s = Settings.parse({"translation_provider": "DeepL API Free"})
        with (
            patch("backend.DeepLTranslator", return_value=self.client),
            patch("backend.Translator") as local,
        ):
            self.assertIs(p.make_translator(), self.client)
            local.assert_not_called()


if __name__ == "__main__":
    unittest.main()
