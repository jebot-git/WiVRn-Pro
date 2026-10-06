#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")"
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/spatial-subtitles"
uv venv --python 3.12 --allow-existing "$runtime/venv"
uv pip install --python "$runtime/venv/bin/python" -r requirements.txt --index https://download.pytorch.org/whl/cpu --index https://pypi.org/simple --index-strategy unsafe-best-match
PYTHONPATH="$PWD/python" "$runtime/venv/bin/python" - <<'PY'
from huggingface_hub import snapshot_download,hf_hub_download
from pathlib import Path
import shutil
from local_recognition import LocalRecognizer,local_root
root=local_root();models=root/'models';models.mkdir(parents=True,exist_ok=True)
snapshot_download('OpenVINO/whisper-large-v3-turbo-int8-ov',revision='b568445dd5dc8c695bde596f8acbb4694fd6ba64',local_dir=models/'whisper-large-v3-turbo')
r=LocalRecognizer(download=True);r.load_ocr()
p=models/'speaker-cjk';p.mkdir(exist_ok=True)
if not (p/'speaker.onnx').exists():
 source=hf_hub_download('Wespeaker/wespeaker-voxceleb-resnet34-LM','voxceleb_resnet34_LM.onnx',revision='f0c48c298fd835726c27956a5d617bad7115627e');shutil.copy2(source,p/'speaker.onnx')
print('Base recognition installed. Select a completed local translation export in settings; there is no external fallback.')
PY
