#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
import argparse, json, statistics, sys, unicodedata, wave
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


p = argparse.ArgumentParser()
p.add_argument("--candidate", required=True)
a = p.parse_args()
rows = json.loads((ROOT / "training/fleurs/manifest.json").read_text())["rows"]
data = []
for language in ["ja", "ko", "zh", "th"]:
    data.extend(
        [r for r in rows if r["split"] == "validation" and r["language"] == language]
    )
report = {}
for name, path in [
    ("baseline", ROOT / "models/whisper-large-v3-turbo"),
    ("candidate", Path(a.candidate)),
]:
    r = LocalRecognizer(root=ROOT, speech_model=str(path))
    r.load_speech()
    times = []
    totals = {}
    results = []
    for row in data:
        with wave.open(row["path"], "rb") as f:
            pcm = f.readframes(f.getnframes())
        # Both systems get the same source-language hint. No timestamps/diarization.
        result = r.speech(pcm, language=row["language"])
        hyp = result["text"]
        ref = normalize(row["text"])
        edits = distance(ref, normalize(hyp))
        total = totals.setdefault(row["language"], [0, 0])
        total[0] += edits
        total[1] += len(ref)
        times.append(result["seconds"])
        results.append(
            dict(
                language=row["language"],
                id=row["id"],
                seconds=result["seconds"],
                reference=row["text"],
                text=hyp,
            )
        )
    report[name] = dict(
        cer={k: v[0] / v[1] for k, v in totals.items()},
        median_seconds=statistics.median(times[1:]),
        p95_seconds=sorted(times[1:])[int(len(times[1:]) * 0.95)],
        rows=results,
    )
    print(name, {k: v for k, v in report[name].items() if k != "rows"}, flush=True)
    del r
report["passes_gate"] = (
    all(
        report["candidate"]["cer"][k] <= v for k, v in report["baseline"]["cer"].items()
    )
    and report["candidate"]["median_seconds"]
    <= report["baseline"]["median_seconds"] * 1.05
)
(ROOT / "training/speech-openvino-benchmark.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
