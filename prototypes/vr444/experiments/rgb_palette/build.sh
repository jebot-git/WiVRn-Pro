#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../../.." && pwd)
exp="$root/prototypes/vr444/experiments/rgb_palette"
ref="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
lz="$root/prototypes/vr444/experiments/native_lz4"
direct="$root/prototypes/vr444/experiments/latency_decode"
out=${1:-/tmp/wivrn-rgb-palette-build}
mkdir -p "$out"
flags=(-std=c++20 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$ref" -I "$lz" -I "$direct")
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer \
  "$exp/rgb_palette.cpp" "$exp/test.cpp" "$ref/codec.cpp" -o "$out/test"
clang -O3 -DNDEBUG -c "$lz/vendor/lz4.c" -o "$out/lz4.o"
clang++ "${flags[@]}" -O3 -DNDEBUG "$exp/rgb_palette.cpp" "$exp/bench.cpp" \
  "$ref/codec.cpp" "$direct/direct.cpp" "$out/lz4.o" -o "$out/bench"
printf '%s\n' "$out/test" "$out/bench"
