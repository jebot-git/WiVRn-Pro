#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/mapped_input_decode"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
lz="$root/prototypes/vr444/experiments/native_lz4"
out=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
validator=${SPIRV_VAL:-"$ndk/shader-tools/linux-x86_64/spirv-val"}
mkdir -p "$out"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$lz")
sources=("$experiment/probe.cpp" "$reference/codec.cpp")
clang -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-host.o"
clang++ "${flags[@]}" "${sources[@]}" "$out/lz4-host.o" -lvulkan -o "$out/mapped-input-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang" -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-android.o"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" "$out/lz4-android.o" -static-libstdc++ -lvulkan -o "$out/mapped-input-android"
for family in rrf rrb; do
    dir="$out/$family/shaders"
    shader="$root/prototypes/vr444/experiments/rowblocks_gpu/decode.comp"
    defines=()
    if [[ "$family" == rrf ]]; then
        dir="$out/rrf/shaders-4"
        shader="$root/prototypes/vr444/experiments/rowfilter_gpu/decode.comp"
        defines=(-DCOLUMNS=4)
    fi
    mkdir -p "$dir"
    for mode in buffer unorm uint; do
        args=("${defines[@]}")
        if [[ "$mode" != buffer ]]; then args+=("-DOUTPUT_${mode^^}=1"); fi
        glslc --target-env=vulkan1.1 -O "${args[@]}" "$shader" -o "$dir/$mode.spv"
        "$validator" --target-env vulkan1.1 "$dir/$mode.spv"
    done
    glslc --target-env=vulkan1.1 -O "$root/prototypes/vr444/experiments/direct_image_gpu/sample.comp" -o "$dir/sample.spv"
    "$validator" --target-env vulkan1.1 "$dir/sample.spv"
done
clang -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -c "$lz/vendor/lz4.c" -o "$out/lz4-asan.o"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "${sources[@]}" "$out/lz4-asan.o" -lvulkan -o "$out/mapped-input-asan"

clang++ "${flags[@]}" "$experiment/pair_test.cpp" "$reference/codec.cpp" "$out/lz4-host.o" -lvulkan -o "$out/pair-test-host"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$experiment/pair_test.cpp" "$reference/codec.cpp" "$out/lz4-asan.o" -lvulkan -o "$out/pair-test-asan"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "$experiment/pair_test.cpp" "$reference/codec.cpp" "$out/lz4-android.o" -static-libstdc++ -lvulkan -o "$out/pair-test-android"
clang++ "${flags[@]}" "$experiment/capabilities.cpp" -lvulkan -o "$out/capabilities-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "$experiment/capabilities.cpp" -static-libstdc++ -lvulkan -o "$out/capabilities-android"
