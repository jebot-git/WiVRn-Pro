#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
exp="$root/prototypes/vr444/experiments/latency_decode"
ref="$root/.worktrees/wivrn-codec-tests/common/codec_test/vr444"
out=${1:-"$root/validation/results/latency-critical-0.13.0/decode/build-upload"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$out"
flags=(-std=c++20 -O3 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$ref")
sources=("$exp/upload_probe.cpp" "$exp/direct.cpp" "$ref/codec.cpp")
clang++ "${flags[@]}" "${sources[@]}" -lvulkan -o "$out/upload-probe-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" -static-libstdc++ -lvulkan -o "$out/upload-probe-android"
