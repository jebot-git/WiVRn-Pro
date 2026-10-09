#!/usr/bin/env bash
set -euo pipefail
experiment=$(cd -- "$(dirname -- "$0")" && pwd)
output=${1:?Pass output directory}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$output"
clang++ -std=c++20 -O2 "$experiment/probe.cpp" -lvulkan -o "$output/probe-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" -std=c++20 -O2 -static-libstdc++ "$experiment/probe.cpp" -lvulkan -o "$output/probe-android"
