#!/usr/bin/env bash
set -euo pipefail
exp=$(cd "$(dirname "$0")" && pwd)
out=${1:-/tmp/wivrn-wireless-pair-build}
mkdir -p "$out"
flags=(-std=c++20 -pthread -Wall -Wextra -Werror)
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$exp/test.cpp" -o "$out/test"
clang++ "${flags[@]}" -O3 -DNDEBUG "$exp/probe.cpp" -o "$out/probe"
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++"
"$compiler" "${flags[@]}" -O3 -DNDEBUG -static-libstdc++ "$exp/probe.cpp" -o "$out/probe-android"
"$compiler" "${flags[@]}" -O3 -DNDEBUG -static-libstdc++ "$exp/test.cpp" -o "$out/test-android"
"$out/test"
sha256sum "$out/probe" "$out/probe-android" "$out/test" "$out/test-android"
