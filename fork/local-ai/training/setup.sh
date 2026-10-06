#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/wivrn-pro/training"
uv venv --python 3.12 --allow-existing "$runtime/venv"
uv pip install --python "$runtime/venv/bin/python" torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/xpu
uv pip install --python "$runtime/venv/bin/python" 'transformers==4.57.6' peft accelerate soundfile scipy pyarrow 'optimum-intel[openvino]' onnx rapidocr==3.9.2 kaldi-native-fbank==1.22.3
# Keep torchvision on XPU; a generic PyPI wheel is incompatible with XPU torch.
uv pip install --python "$runtime/venv/bin/python" torchvision==0.29.1 --index-url https://download.pytorch.org/whl/xpu --reinstall-package torchvision
"$runtime/venv/bin/python" - <<'PY'
from huggingface_hub import snapshot_download
from pathlib import Path
import os
root=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'wivrn-pro/training'
snapshot_download('openai/whisper-large-v3-turbo',revision='41f01f3fe87f28c78e2fbf8b568835947dd65ed9',
    allow_patterns=['*.json','*.txt','*.safetensors'],local_dir=root/'base')
PY
