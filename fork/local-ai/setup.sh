#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/../.."
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/wivrn-pro/local-ai"
uv venv --python 3.12 --allow-existing "$runtime/venv"
uv pip install --python "$runtime/venv/bin/python" -r fork/local-ai/requirements.txt
PYTHONPATH="$PWD/dashboard/shora" "$runtime/venv/bin/python" - <<'PY'
from huggingface_hub import snapshot_download, hf_hub_download
from pathlib import Path
import shutil
from local_recognition import LocalRecognizer, local_root
snapshot_download('OpenVINO/whisper-large-v3-turbo-int8-ov',
    revision='b568445dd5dc8c695bde596f8acbb4694fd6ba64',
    local_dir=local_root()/'models/whisper-large-v3-turbo')
r = LocalRecognizer(download=True)
r.load_speech(); r.load_ocr()
# Initial setup installs the pretrained speaker backbone. Training exports live
# in separate directories; never overwrite a locally adapted model here.
speaker=local_root()/'models/speaker-cjk';speaker.mkdir(parents=True,exist_ok=True)
if not (speaker/'speaker.onnx').exists():
    source=hf_hub_download('Wespeaker/wespeaker-voxceleb-resnet34-LM','voxceleb_resnet34_LM.onnx',
        revision='f0c48c298fd835726c27956a5d617bad7115627e')
    shutil.copy2(source,speaker/'speaker.onnx')
print('Ready:', r.device, r.device_name, 'OCR execution devices:', r.compiled_devices)
PY
