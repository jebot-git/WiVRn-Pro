# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline-only NLLB translation. Model paths are local and downloads forbidden."""

from collections import OrderedDict
from pathlib import Path
import os, threading

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
CODES = {
    "Japanese": "jpn_Jpan",
    "Korean": "kor_Hang",
    "Chinese": "zho_Hans",
    "Thai": "tha_Thai",
    "English": "eng_Latn",
    "Serbian": "srp_Cyrl",
    "Russian": "rus_Cyrl",
}


def detect(text):
    if any("\u0e00" <= c <= "\u0e7f" for c in text):
        return "Thai"
    if any("\uac00" <= c <= "\ud7af" for c in text):
        return "Korean"
    if any("\u3040" <= c <= "\u30ff" for c in text):
        return "Japanese"
    if any("\u4e00" <= c <= "\u9fff" for c in text):
        return "Chinese"
    if any("\u0400" <= c <= "\u052f" for c in text):
        return "Russian"
    return "English"


class Translator:
    def __init__(self, path, device, cache):
        from transformers import AutoTokenizer
        from optimum.intel.openvino import OVModelForSeq2SeqLM

        path = Path(path)
        if not path.is_dir() or not (path / "openvino_encoder_model.xml").is_file():
            raise ValueError("Install a completed local translation export")
        self.tokenizer = AutoTokenizer.from_pretrained(
            path, local_files_only=True, trust_remote_code=False
        )
        self.model = OVModelForSeq2SeqLM.from_pretrained(
            str(path),
            device=device,
            local_files_only=True,
            trust_remote_code=False,
            compile=True,
            ov_config={"CACHE_DIR": str(cache), "PERFORMANCE_HINT": "LATENCY"},
        )
        self.cache = OrderedDict()
        self.lock = threading.Lock()

    def translate(self, text, target, source="Auto"):
        if target == "Original":
            return text
        source = detect(text) if source == "Auto" else source
        if source == target:
            return text
        key = (text, target, source)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            # The adaptation includes an English→Serbian pair; pivot stays on this GPU.
            result = (
                self._one(text, source, "English")
                if target == "Serbian" and source != "English"
                else text
            )
            result = self._one(
                result, "English" if target == "Serbian" else source, target
            )
            self.cache[key] = result
            if len(self.cache) > 256:
                self.cache.popitem(last=False)
            return result

    def _one(self, text, source, target):
        self.tokenizer.src_lang = CODES[source]
        inputs = self.tokenizer(
            text, return_tensors="pt", truncation=True, max_length=256
        )
        generated = self.model.generate(
            **inputs,
            forced_bos_token_id=self.tokenizer.convert_tokens_to_ids(CODES[target]),
            max_new_tokens=128,
            num_beams=1,
            do_sample=False,
        )
        return self.tokenizer.batch_decode(generated, skip_special_tokens=True)[
            0
        ].strip()[:360]
