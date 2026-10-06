#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
import json, statistics, sys, wave, unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from local_recognition import LocalRecognizer
from settings import ROOT
from rapidfuzz.distance.Levenshtein import distance


def normalize(text):
    return "".join(
        c
        for c in unicodedata.normalize("NFKC", text).lower()
        if not c.isspace() and not unicodedata.category(c).startswith("P")
    )


r = LocalRecognizer(root=ROOT, speech_model=str(ROOT / "models/speech-cjk-th-001-int8"))
r.load_speech()
data = json.loads((ROOT / "training/fleurs/manifest.json").read_text())["rows"]
report = {}
for language in ["ja", "ko", "zh", "th"]:
    rows = [
        x for x in data if x["split"] == "validation" and x["language"] == language
    ][:8]
    times = {"timestamps": [], "phrase": []}
    errors = {"timestamps": 0, "phrase": 0}
    total = 0
    for i, row in enumerate(rows):
        with wave.open(row["path"], "rb") as f:
            pcm = f.readframes(f.getnframes())
        reference = normalize(row["text"])
        total += len(reference)
        # Counterbalanced order; embeddings and reference audio never leave the process.
        modes = ["timestamps", "phrase"] if i % 2 else ["phrase", "timestamps"]
        for mode in modes:
            z = r.speech(
                pcm,
                language=language,
                distinguish=True,
                timestamps=mode == "timestamps",
            )
            times[mode].append(z["seconds"])
            errors[mode] += distance(reference, normalize(z["text"]))
    report[language] = dict(
        median_seconds={k: statistics.median(v[1:]) for k, v in times.items()},
        cer={k: v / total for k, v in errors.items()},
        samples=len(rows),
    )
    print(language, report[language], flush=True)
(ROOT / "training/voice-mode-benchmark.json").write_text(json.dumps(report, indent=2))
