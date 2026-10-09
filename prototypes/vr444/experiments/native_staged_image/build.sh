#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/native_staged_image"
native="$root/validation/wivrn-codecs/integration/source-overlay/client/decoder"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
out=${1:?usage: build.sh ABSOLUTE_OUTPUT_DIRECTORY}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
validator=${SPIRV_VAL:-"$ndk/shader-tools/linux-x86_64/spirv-val"}
mkdir -p "$out/shaders"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$native")
sources=("$experiment/test.cpp" "$native/staged_rowblocks_image.cpp")
clang++ "${flags[@]}" "${sources[@]}" -lvulkan -o "$out/staged-image-test"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "${sources[@]}" -lvulkan -o "$out/staged-image-test-asan"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" -static-libstdc++ -lvulkan -o "$out/staged-image-test-android"
glslc --target-env=vulkan1.1 -O "$native/staged_rowblocks_image.comp" -o "$out/shaders/staged.spv"
glslc --target-env=vulkan1.1 -O "$experiment/sample.comp" -o "$out/shaders/sample.spv"
# Existing oracle creates all descriptor layouts, but only the CPU reference
# image upload and sample pipeline are executed by this new test.
for mode in buffer unorm uint; do
    args=()
    if [[ "$mode" != buffer ]]; then args+=("-DOUTPUT_${mode^^}=1"); fi
    glslc --target-env=vulkan1.1 -O "${args[@]}" "$root/prototypes/vr444/experiments/rowblocks_gpu/decode.comp" -o "$out/shaders/$mode.spv"
done
for shader in "$out/shaders/"*.spv; do "$validator" --target-env vulkan1.1 "$shader"; done
