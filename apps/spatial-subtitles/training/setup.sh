#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
runtime="${XDG_DATA_HOME:-$HOME/.local/share}/spatial-subtitles/training"
uv venv --python 3.12 --allow-existing "$runtime/venv"
uv pip install --python "$runtime/venv/bin/python" torch==2.14.1 torchvision==0.29.1 --index-url https://download.pytorch.org/whl/xpu
uv pip install --python "$runtime/venv/bin/python" 'transformers==4.57.6' peft accelerate soundfile scipy pyarrow 'optimum-intel[openvino]' onnx 'rapidocr==3.9.2' 'kaldi-native-fbank==1.22.3' sacrebleu sentencepiece pillow rapidfuzz
# Keep torchvision matched to the Intel XPU torch wheel.
uv pip install --python "$runtime/venv/bin/python" torchvision==0.29.1 --index-url https://download.pytorch.org/whl/xpu --reinstall-package torchvision
export SPATIAL_TRAIN_ROOT="$runtime"
"$runtime/venv/bin/python" - <<'PY'
import os
from pathlib import Path
from huggingface_hub import snapshot_download
root=Path(os.environ['SPATIAL_TRAIN_ROOT'])
snapshot_download('openai/whisper-large-v3-turbo',revision='41f01f3fe87f28c78e2fbf8b568835947dd65ed9',local_dir=root/'whisper-base',allow_patterns=['*.json','*.txt','*.model','*.tiktoken','*.safetensors','README.md'])
print('Local XPU training environment ready:',root)
PY
