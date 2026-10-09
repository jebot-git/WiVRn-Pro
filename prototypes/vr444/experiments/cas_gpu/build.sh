#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/cas_gpu"
output=${1:-"$root/validation/results/codec-viability-0.8.0/cas/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$output/shaders"
flags=(-std=c++17 -O2 -ffp-contract=off -Wall -Wextra -Wno-unused-function -Wno-missing-field-initializers -Wno-misleading-indentation -I "$experiment")
clang++ "${flags[@]}" "$experiment/bench.cpp" -lvulkan -o "$output/cas_bench"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "$experiment/bench.cpp" -static-libstdc++ -lvulkan -o "$output/cas_bench_android"
"$ndk/shader-tools/linux-x86_64/glslc" --target-env=vulkan1.1 -O -DBYPASS=1 "$experiment/cas.comp" -o "$output/shaders/bypass.spv"
"$ndk/shader-tools/linux-x86_64/glslc" --target-env=vulkan1.1 -O -DSHARPEN_ONLY=1 "$experiment/cas.comp" -o "$output/shaders/sharpen.spv"
"$ndk/shader-tools/linux-x86_64/glslc" --target-env=vulkan1.1 -O "$experiment/cas.comp" -o "$output/shaders/upscale.spv"
