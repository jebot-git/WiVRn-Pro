#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/palette_output_memory"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
palette="$root/prototypes/vr444/experiments/rgb_palette"
mkdir -p "$experiment/build"
clang++ -std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -I "$reference" -I "$palette" -I "$root/prototypes/vr444/experiments/native_lz4" \
  "$experiment/bench.cpp" "$palette/rgb_palette.cpp" "$reference/codec.cpp" \
  -lvulkan -o "$experiment/build/palette-bench"
