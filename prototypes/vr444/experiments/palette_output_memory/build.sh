#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/palette_output_memory"
encoder="$root/prototypes/vr444/experiments/palette_gpu_encode"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
palette="$root/prototypes/vr444/experiments/rgb_palette"
output=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$output/shaders"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$palette" -I "$root/prototypes/vr444/experiments/native_lz4")
sources=("$experiment/probe.cpp" "$palette/rgb_palette.cpp" "$reference/codec.cpp")
clang++ "${flags[@]}" "${sources[@]}" -lvulkan -o "$output/palette-output-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" -static-libstdc++ -lvulkan -o "$output/palette-output-android"
for stage in encode scan compact; do
    glslc --target-env=vulkan1.1 -O "$encoder/$stage.comp" -o "$output/shaders/$stage.spv"
done
