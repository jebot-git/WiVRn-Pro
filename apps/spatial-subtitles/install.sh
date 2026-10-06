#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")"
prefix="${SPATIAL_SUBTITLES_PREFIX:-$HOME/.local/opt/spatial-subtitles}"
cmake -S . -B build -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$prefix"
cmake --build build --parallel 4
cmake --install build
export SPATIAL_SUBTITLES_PREFIX="$prefix"
python3 install_config.py
systemctl --user daemon-reload
