#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
import json, statistics, sys, wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from local_recognition import LocalRecognizer
from settings import ROOT

r = LocalRecognizer(root=ROOT)
r.load_speech()
rows = json.loads((ROOT / "training/fleurs/manifest.json").read_text())["rows"]
report = {}
for lang in ["ja", "ko", "zh", "th"]:
    group = [x for x in rows if x["language"] == lang and x["split"] == "validation"][
        :8
    ]
    times = {"auto": [], "hint": []}
    outputs = []
    for row in group:
        with wave.open(row["path"], "rb") as f:
            pcm = f.readframes(f.getnframes())
        result = {}
        for mode in ["auto", "hint"]:
            z = r.speech(pcm, language=lang if mode == "hint" else None)
            times[mode].append(z["seconds"])
            result[mode] = z["text"]
        outputs.append(dict(id=row["id"], reference=row["text"], **result))
    report[lang] = dict(
        median_seconds={k: statistics.median(v[1:]) for k, v in times.items()},
        rows=outputs,
    )
    print(lang, report[lang]["median_seconds"], flush=True)
(ROOT / "training/speech-hint-benchmark.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
