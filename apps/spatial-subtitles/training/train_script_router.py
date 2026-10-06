#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Train a small OCR script router to avoid running every recognition head."""

import json, random
from pathlib import Path
import numpy as np, torch, openvino as ov
from PIL import Image, ImageDraw, ImageFont

root = Path.home() / ".local/share/spatial-subtitles"
modelroot = root / "models/ocr-cjk-th-001"
torch.manual_seed(173)
random.seed(173)
torch.set_num_threads(4)
devices = [
    i for i in range(torch.xpu.device_count()) if "A770" in torch.xpu.get_device_name(i)
]
assert len(devices) == 1
device = f"xpu:{devices[0]}"
rows = json.loads((root / "training/fleurs/manifest.json").read_text())["rows"]
data = []


def pixels(image):
    image = image.convert("L")
    image.thumbnail((192, 32))
    canvas = Image.new("L", (192, 32), 128)
    canvas.paste(image, (0, (32 - image.height) // 2))
    return np.asarray(canvas, dtype=np.float32)[None] / 127.5 - 1


for i, r in enumerate(rows):
    path = modelroot / "examples" / r["split"] / f"{i}.png"
    if path.exists():
        data.append(
            (
                pixels(Image.open(path)),
                {"ja": 0, "zh": 0, "ko": 1, "th": 2}[r["language"]],
                r["split"],
            )
        )
# Serbian Cyrillic adds the fourth script without changing the existing Cyrillic OCR weights.
for i, r in enumerate(
    json.loads((root / "training/translation.json").read_text())["rows"]
):
    if r["language"] != "en" or r["split"] == "test":
        continue
    text = r["target"][:20]
    font = ImageFont.truetype("/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf", 26)
    b = font.getbbox(text)
    im = Image.new("L", (b[2] - b[0] + 12, b[3] - b[1] + 12), 235)
    ImageDraw.Draw(im).text((6 - b[0], 6 - b[1]), text, font=font, fill=25)
    data.append((pixels(im), 3, r["split"]))
model = torch.nn.Sequential(
    torch.nn.Conv2d(1, 16, 3, padding=1),
    torch.nn.ReLU(),
    torch.nn.MaxPool2d(2),
    torch.nn.Conv2d(16, 32, 3, padding=1),
    torch.nn.ReLU(),
    torch.nn.MaxPool2d(2),
    torch.nn.Conv2d(32, 64, 3, padding=1),
    torch.nn.ReLU(),
    torch.nn.AdaptiveAvgPool2d((2, 6)),
    torch.nn.Flatten(),
    torch.nn.Linear(768, 4),
).to(device)
train = [r for r in data if r[2] == "train"]
valid = [r for r in data if r[2] == "validation"]
optim = torch.optim.AdamW(model.parameters(), lr=0.001)
for epoch in range(30):
    random.shuffle(train)
    model.train()
    losses = []
    for start in range(0, len(train), 32):
        batch = train[start : start + 32]
        x = torch.from_numpy(np.stack([r[0] for r in batch])).to(device)
        y = torch.tensor([r[1] for r in batch], device=device)
        # Polarity and mild sensor noise are randomized only in training.
        if random.random() < 0.5:
            x = -x
        x = x + torch.randn_like(x) * 0.02
        optim.zero_grad()
        loss = torch.nn.functional.cross_entropy(model(x), y)
        loss.backward()
        optim.step()
        losses.append(loss.item())
    if epoch % 5 == 0:
        print(epoch, sum(losses) / len(losses), flush=True)
model.eval()
x = torch.from_numpy(np.stack([r[0] for r in valid])).to(device)
y = np.array([r[1] for r in valid])
with torch.no_grad():
    p = model(x).softmax(-1).cpu().numpy()
accepted = p.max(-1) >= 0.95
report = dict(
    train=len(train),
    validation=len(valid),
    accuracy=float((p.argmax(-1) == y).mean()),
    confident_coverage=float(accepted.mean()),
    confident_errors=int(((p.argmax(-1) != y) & accepted).sum()),
    threshold=0.95,
    labels=["cjk", "korean", "thai", "cyrillic"],
    warning="Synthetic crops only; uncertain routes run all heads. Real scene validation required.",
)
model.cpu()
converted = ov.convert_model(
    model, example_input=torch.zeros(1, 1, 32, 192), input=[-1, 1, 32, 192]
)
ov.save_model(converted, modelroot / "script-router.xml")
(modelroot / "script-router-report.json").write_text(json.dumps(report, indent=2))
print(report, flush=True)
