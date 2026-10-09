#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/vulkan_extensions"
output=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$output"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers)
for name in capabilities timeline_probe; do
    clang++ "${flags[@]}" "$experiment/$name.cpp" -lvulkan -o "$output/$name-host"
    "$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "$experiment/$name.cpp" -static-libstdc++ -lvulkan -o "$output/$name-android"
done
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$experiment/ring_test.cpp" -o "$output/ring-test"
