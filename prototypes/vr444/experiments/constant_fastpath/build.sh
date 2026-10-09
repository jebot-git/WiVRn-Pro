#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/constant_fastpath"
reference="$root/.worktrees/wivrn-codec-tests/common/codec_test/vr444"
output=${1:-"$root/validation/results/codec-development-0.11.0/gpu/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$output/base" "$output/fast"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference")
sources=("$experiment/probe.cpp" "$experiment/runner.cpp" "$reference/codec.cpp")
clang++ "${flags[@]}" "${sources[@]}" -lvulkan -o "$output/probe"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" -static-libstdc++ -lvulkan -o "$output/probe-android"
glslc="$ndk/shader-tools/linux-x86_64/glslc"
for stage in encode decode; do
 "$glslc" --target-env=vulkan1.1 -O -D"${stage^^}"=1 -DWORKGROUP=128 "$reference/shaders/codec_coop.comp" -o "$output/base/$stage-coop128.spv"
done
cp "$output/base/encode-coop128.spv" "$output/fast/encode-coop128.spv"
"$glslc" --target-env=vulkan1.1 -O -DDECODE=1 -DWORKGROUP=128 "$experiment/constant_decode.comp" -o "$output/fast/decode-coop128.spv"
