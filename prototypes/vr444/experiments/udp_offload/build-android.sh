#!/usr/bin/env bash
set -euo pipefail
exp=$(cd "$(dirname "$0")" && pwd)
out=${1:-/tmp/wivrn-udp-offload-build}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++"
test -x "$compiler"
mkdir -p "$out"
"$compiler" -std=c++20 -O3 -DNDEBUG -Wall -Wextra -Werror -static-libstdc++ \
  "$exp/bench.cpp" -o "$out/probe-android"
printf '%s\n' "$out/probe-android (cross-compiled only; no device run)"
