#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/cqp-tests
read -r -a ffmpeg_flags <<< "$(pkg-config --cflags --libs libavcodec libavutil)"
"${CXX:-c++}" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/cqp_rate_control_test.cpp "${ffmpeg_flags[@]}" -o build/cqp-tests/rate-control
build/cqp-tests/rate-control
python3 fork/encoder-presets/test_presets.py
if [[ ${1:-} == --hardware ]]; then
  "${CXX:-c++}" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/cqp_qsv_smoke.cpp "${ffmpeg_flags[@]}" -o build/cqp-tests/qsv-smoke
  build/cqp-tests/qsv-smoke
fi
