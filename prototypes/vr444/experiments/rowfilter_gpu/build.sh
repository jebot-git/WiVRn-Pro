#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/rowfilter_gpu"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
lz="$root/prototypes/vr444/experiments/native_lz4"
out=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$out"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$lz")
sources=("$experiment/probe.cpp" "$reference/codec.cpp")
clang -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-host.o"
clang++ "${flags[@]}" "${sources[@]}" "$out/lz4-host.o" -lvulkan -o "$out/rowfilter-gpu-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang" -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-android.o"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" "$out/lz4-android.o" -static-libstdc++ -lvulkan -o "$out/rowfilter-gpu-android"
for columns in 1 4 16; do
    mkdir -p "$out/shaders-$columns"
    for mode in buffer unorm uint; do
        args=("-DCOLUMNS=$columns")
        if [[ "$mode" != buffer ]]; then args+=("-DOUTPUT_${mode^^}=1"); fi
        glslc --target-env=vulkan1.1 -O "${args[@]}" "$experiment/decode.comp" -o "$out/shaders-$columns/$mode.spv"
    done
    glslc --target-env=vulkan1.1 -O "$root/prototypes/vr444/experiments/direct_image_gpu/sample.comp" -o "$out/shaders-$columns/sample.spv"
done
