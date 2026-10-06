# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional text-only DeepL API Free adapter; no configurable URL or redirects."""

from collections import OrderedDict
import http.client
import json
import os
import stat
import time
from settings import CONFIG, atomic_json

KEY_FILE = CONFIG.with_name("deepl-key.json")
LANGUAGES = {
    "English": "EN",
    "Serbian": "SR",
    "Japanese": "JA",
    "Korean": "KO",
    "Chinese": "ZH",
    "Thai": "TH",
    "Russian": "RU",
}


def save_key(value):
    value = value.strip()
    if not value or len(value) > 512 or any(c.isspace() or ord(c) < 33 for c in value):
        raise ValueError("Enter a valid DeepL API Free key")
    atomic_json(KEY_FILE, {"key": value})


def read_key():
    try:
        fd = os.open(KEY_FILE, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd) as f:
            s = os.fstat(f.fileno())
            if (
                not stat.S_ISREG(s.st_mode)
                or s.st_uid != os.getuid()
                or s.st_mode & 0o077
                or s.st_size > 4096
            ):
                raise ValueError("DeepL key file must be private (mode 0600)")
            value = json.load(f)["key"]
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 512
            or any(c.isspace() or ord(c) < 33 for c in value)
        ):
            raise ValueError("Invalid DeepL key")
        return value
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError(
            "Add your DeepL API Free key in Spatial Subtitles settings"
        ) from None


def key_stamp():
    try:
        return KEY_FILE.stat().st_mtime_ns
    except OSError:
        return 0


class DeepLTranslator:
    def __init__(self, cancelled=None):
        self.cancelled = cancelled
        self._key = read_key()
        self.cache = OrderedDict()
        self.retry_at = 0
        self.error = ""

    def translate(self, text, target, source="Auto"):
        return self.translate_many([text], target, source)[0]

    def translate_many(self, texts, target, source="Auto"):
        if target == "Original" or source == target:
            return list(texts)
        if target not in LANGUAGES or (source != "Auto" and source not in LANGUAGES):
            raise ValueError("Unsupported DeepL language")
        if len(texts) > 50 or any(
            not isinstance(t, str) or len(t) > 4096 for t in texts
        ):
            raise ValueError("DeepL text batch exceeds subtitle limits")
        keys = [(t, target, source) for t in texts]
        missing = list(
            dict.fromkeys(k for k in keys if k[0].strip() and k not in self.cache)
        )
        if missing:
            if time.monotonic() < self.retry_at:
                raise RuntimeError(self.error)
            payload = {
                "text": [k[0] for k in missing],
                "target_lang": LANGUAGES[target],
                "model_type": "prefer_quality_optimized",
            }
            # Let DeepL detect Japanese/Chinese rather than guessing from script.
            if source != "Auto":
                payload["source_lang"] = LANGUAGES[source]
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if len(body) > 128 * 1024:
                raise ValueError("DeepL request exceeds 128 KiB")
            if self.cancelled is not None and self.cancelled.is_set():
                raise RuntimeError("Translation settings changed")
            connection = http.client.HTTPSConnection("api-free.deepl.com", timeout=4)
            delay = 10
            try:
                connection.request(
                    "POST",
                    "/v2/translate",
                    body=body,
                    headers={
                        "Authorization": "DeepL-Auth-Key " + self._key,
                        "Content-Type": "application/json",
                        "User-Agent": "SpatialSubtitles/1.0",
                    },
                )
                response = connection.getresponse()
                if response.status != 200:
                    delay = 300 if response.status in (403, 456) else 15
                    message = {
                        400: "DeepL rejected the language or translation request",
                        403: "DeepL rejected the API key; check your API Free account",
                        429: "DeepL rate limit reached; retrying after a pause",
                        456: "DeepL Free character quota exhausted",
                    }.get(
                        response.status,
                        "DeepL is unavailable (HTTP " + str(response.status) + ")",
                    )
                    raise RuntimeError(message)
                raw = response.read(256 * 1024 + 1)
                if len(raw) > 256 * 1024:
                    raise ValueError("Oversized response")
                translated = json.loads(raw)["translations"]
                if len(translated) != len(missing) or any(
                    not isinstance(x.get("text"), str) or not x["text"].strip()
                    for x in translated
                ):
                    raise ValueError("Invalid translations")
                for key, item in zip(missing, translated):
                    self.cache[key] = item["text"].strip()[:360]
            except RuntimeError as e:
                self.error = str(e)
                self.retry_at = time.monotonic() + delay
                raise
            except (
                OSError,
                http.client.HTTPException,
                ValueError,
                KeyError,
                TypeError,
                AttributeError,
            ):
                # Never include server bodies, submitted text, headers or credentials.
                self.error = "DeepL connection failed or returned an invalid response"
                self.retry_at = time.monotonic() + delay
                raise RuntimeError(self.error) from None
            finally:
                connection.close()
        result = [self.cache[k] if k[0].strip() else k[0] for k in keys]
        for key in keys:
            if key in self.cache:
                self.cache.move_to_end(key)
        while len(self.cache) > 512:
            self.cache.popitem(last=False)
        return result
