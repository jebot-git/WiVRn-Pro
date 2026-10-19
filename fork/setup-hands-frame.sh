#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
# Reuse the local vision environment so installed OCR/translation models remain
# available. This helper installs libraries only; it never downloads model weights.
frame_runtime="${XDG_DATA_HOME:-$HOME/.local/share}/spatial-subtitles"
uv venv --python 3.12 --allow-existing "$frame_runtime/venv"
uv pip install --python "$frame_runtime/venv/bin/python" -r server/wayvr/requirements-hands-frame.txt
ffmpeg -hide_banner -encoders 2>/dev/null | rg 'libvpx-vp9'
