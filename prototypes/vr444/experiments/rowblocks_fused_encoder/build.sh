#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../../.." && pwd)
exp="$root/prototypes/vr444/experiments/rowblocks_fused_encoder"
lz="$root/prototypes/vr444/experiments/native_lz4"
ref="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
direct="$root/prototypes/vr444/experiments/latency_decode"
out=${1:-/tmp/wivrn-rowblocks-fused-encoder-build}
mkdir -p "$out"
flags=(-std=c++20 -pthread -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$lz" -I "$ref" -I "$direct")
clang -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -c "$lz/vendor/lz4.c" -o "$out/lz4-test.o"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$exp/test.cpp" "$ref/codec.cpp" "$direct/direct.cpp" "$out/lz4-test.o" -o "$out/test"
if [[ ${RRB_FUSED_TSAN:-0} == 1 ]]; then
    clang -O1 -g -fsanitize=thread -c "$lz/vendor/lz4.c" -o "$out/lz4-tsan.o"
    clang++ "${flags[@]}" -O1 -g -fsanitize=thread "$exp/test.cpp" "$ref/codec.cpp" "$direct/direct.cpp" "$out/lz4-tsan.o" -o "$out/test-tsan"
fi
clang -O3 -DNDEBUG -c "$lz/vendor/lz4.c" -o "$out/lz4.o"
clang++ "${flags[@]}" -O3 -DNDEBUG "$exp/bench.cpp" "$ref/codec.cpp" "$direct/direct.cpp" "$out/lz4.o" -o "$out/bench"
printf '%s\n' "$out/test" "$out/bench"
