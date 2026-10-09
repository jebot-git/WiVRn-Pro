#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/rowfilter_image_timing"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
lz="$root/prototypes/vr444/experiments/native_lz4"
out=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$out"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$lz")
sources=("$experiment/bench.cpp" "$reference/codec.cpp")
clang -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-host.o"
clang++ "${flags[@]}" "${sources[@]}" "$out/lz4-host.o" -lvulkan -o "$out/rowfilter-image-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang" -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-android.o"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" "$out/lz4-android.o" -static-libstdc++ -lvulkan -o "$out/rowfilter-image-android"
