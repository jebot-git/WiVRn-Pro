import sys, json, statistics
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "python"))
from local_recognition import LocalRecognizer
from settings import ROOT
from PIL import Image, ImageDraw, ImageFont
import numpy as np

font = ROOT / "training/fonts/NotoSansCJK-Regular.ttc"
im = Image.new("RGB", (1024, 1024), (38, 43, 55))
draw = ImageDraw.Draw(im)
lines = [
    ("ja", "これは日本語です", 0),
    ("ko", "여기에 한국어가 있습니다", 1),
    ("zh", "这是中文文字", 2),
    ("th", "ยินดีต้อนรับทุกคน", 0),
    ("sr", "Добро дошли свима", 0),
]
for i, (language, text, index) in enumerate(lines):
    f = ImageFont.truetype(
        str(
            "/usr/share/fonts/google-noto-vf/NotoSansThai[wght].ttf"
            if language == "th"
            else "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf"
            if language == "sr"
            else font
        ),
        36,
        index=index,
    )
    y = 60 + i * 180
    draw.rectangle((25, y - 10, 980, y + 85), fill=(210, 230, 235))
    draw.text((45, y), text, font=f, fill=(25, 40, 75))
im.save(ROOT / "training/ocr-benchmark.png")
image = np.asarray(im)
report = {}
for mode in ["baseline", "trained", "routed"]:
    r = LocalRecognizer(
        root=ROOT,
        ocr_model=str(ROOT / "models/ocr-cjk-th-001") if mode != "baseline" else "",
    )
    r.detector_size = 960 if mode == "baseline" else 640
    r.max_regions = 24 if mode == "baseline" else 8
    r.load_ocr()
    if mode != "routed":
        r.script_router = None
    results = [r.image(image) for _ in range(6)]
    report[mode] = dict(
        median_seconds=statistics.median(x["seconds"] for x in results[1:]),
        regions=results[-1]["regions"],
    )
    print(
        mode,
        report[mode]["median_seconds"],
        [(x["text"], x["script"]) for x in results[-1]["regions"]],
        flush=True,
    )
    del r
(ROOT / "training/ocr-benchmark.json").write_text(
    json.dumps(report, ensure_ascii=False, indent=2)
)
