#!/usr/bin/env bash
set -euo pipefail
research_root=$(cd -- "$(dirname -- "$0")/../../.." && pwd)
research_out=${1:?Pass a new output directory}
test ! -e "$research_out"
mkdir -p "$research_out"
research_out=$(realpath "$research_out")
research_src="$research_root/validation/wivrn-codecs/research"
research_codec="$research_root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
research_flags=(-std=c++20 -O1 -g -Wall -Wextra -Werror -Wno-missing-field-initializers -fno-omit-frame-pointer)
clang++ "${research_flags[@]}" -fsanitize=fuzzer,address,undefined -I "$research_codec" \
  "$research_src/fuzz_decode.cpp" "$research_codec/direct.cpp" "$research_codec/codec.cpp" -o "$research_out/fuzz-decode"
clang++ "${research_flags[@]}" -fsanitize=address,undefined -I "$research_codec" \
  "$research_src/fuzz_seeds.cpp" "$research_codec/codec.cpp" -o "$research_out/fuzz-seeds"
ASAN_OPTIONS=detect_leaks=0:halt_on_error=1 UBSAN_OPTIONS=halt_on_error=1 \
  "$research_out/fuzz-seeds" "$research_out/corpus"
printf '%s\n' "$research_out/fuzz-decode"
