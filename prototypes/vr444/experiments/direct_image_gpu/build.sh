#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/direct_image_gpu"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
output=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$output/shaders"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference")
clang++ "${flags[@]}" "$experiment/probe.cpp" "$reference/codec.cpp" -lvulkan -o "$output/direct-image-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "$experiment/probe.cpp" "$reference/codec.cpp" -static-libstdc++ -lvulkan -o "$output/direct-image-android"
for mode in buffer unorm uint; do
    args=()
    if [[ "$mode" != buffer ]]; then args+=("-DOUTPUT_${mode^^}=1"); fi
    glslc --target-env=vulkan1.1 -O -DWORKGROUP=128 "${args[@]}" "$experiment/decode.comp" -o "$output/shaders/$mode.spv"
done
glslc --target-env=vulkan1.1 -O "$experiment/sample.comp" -o "$output/shaders/sample.spv"
