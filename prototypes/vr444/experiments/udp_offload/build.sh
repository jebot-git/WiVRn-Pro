#!/usr/bin/env bash
set -euo pipefail
exp=$(cd "$(dirname "$0")" && pwd)
out=${1:-/tmp/wivrn-udp-offload-build}
mkdir -p "$out"
flags=(-std=c++20 -Wall -Wextra -Werror)
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$exp/test.cpp" -o "$out/test"
clang++ "${flags[@]}" -O3 -DNDEBUG "$exp/bench.cpp" -o "$out/probe"
printf '%s\n' "$out/test" "$out/probe"
