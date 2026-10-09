#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../../.." && pwd)
exp="$root/prototypes/vr444/experiments/rgb_rowfilter"
lz="$root/prototypes/vr444/experiments/native_lz4"
ref="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
direct="$root/prototypes/vr444/experiments/latency_decode"
out=${1:-/tmp/wivrn-rgb-rowfilter-build}
mkdir -p "$out"
flags=(-std=c++20 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$lz" -I "$ref" -I "$direct")
clang -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -c "$lz/vendor/lz4.c" -o "$out/lz4-test.o"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$exp/test.cpp" "$out/lz4-test.o" -o "$out/test"
clang -O3 -DNDEBUG -c "$lz/vendor/lz4.c" -o "$out/lz4.o"
clang++ "${flags[@]}" -O3 -DNDEBUG "$exp/bench.cpp" "$ref/codec.cpp" "$direct/direct.cpp" "$out/lz4.o" -o "$out/bench"
clang++ "${flags[@]}" -O3 -DNDEBUG "$exp/export.cpp" "$ref/codec.cpp" "$out/lz4.o" -o "$out/export"
printf '%s\n' "$out/test" "$out/bench" "$out/export"
