import json, time, statistics, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from settings import ROOT
from translation import Translator

rows = json.loads((ROOT / "training/translation.json").read_text())["rows"]
report = {}
langs = {
    "ja": "Japanese",
    "ko": "Korean",
    "zh": "Chinese",
    "th": "Thai",
    "en": "English",
}
for model in ["translation-cjk-th-001", "translation-cjk-th-001-int8"]:
    t = Translator(ROOT / "models" / model, "GPU.1", ROOT / "cache")
    times = []
    out = []
    for lang in langs:
        group = [
            r for r in rows if r["language"] == lang and r["split"] == "validation"
        ][:12]
        for r in group:
            start = time.monotonic()
            text = t.translate(
                r["source"],
                "Serbian" if r["output"] == "sr" else "English",
                langs[lang],
            )
            elapsed = time.monotonic() - start
            times.append(elapsed)
            out.append(
                dict(language=lang, reference=r["target"], text=text, seconds=elapsed)
            )
        print(model, lang, "done", flush=True)
    from sacrebleu.metrics import CHRF

    metric = CHRF(word_order=2)
    report[model] = dict(
        median_seconds=statistics.median(times),
        p95_seconds=sorted(times)[int(len(times) * 0.95)],
        languages={
            l: metric.corpus_score(
                [r["text"] for r in out if r["language"] == l],
                [[r["reference"] for r in out if r["language"] == l]],
            ).score
            for l in langs
        },
        rows=out,
    )
    del t
(ROOT / "training/translation-openvino-benchmark.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
print(
    {k: {x: y for x, y in v.items() if x != "rows"} for k, v in report.items()},
    flush=True,
)
