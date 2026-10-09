#!/usr/bin/env bash
set -euo pipefail
exp=$(cd "$(dirname "$0")" && pwd)
out=${1:-/tmp/wivrn-udp-batch-build}
mkdir -p "$out"
clang++ -std=c++20 -O1 -g -Wall -Wextra -Werror -fsanitize=address,undefined \
  -fno-omit-frame-pointer "$exp/probe.cpp" -o "$out/test"
clang++ -std=c++20 -O3 -DNDEBUG -Wall -Wextra -Werror "$exp/probe.cpp" -o "$out/probe"
printf '%s\n' "$out/test" "$out/probe"
