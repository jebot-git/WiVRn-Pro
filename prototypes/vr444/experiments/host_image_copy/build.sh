#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/host_image_copy"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
lz="$root/prototypes/vr444/experiments/native_lz4"
out=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$out"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$lz")
sources=("$experiment/probe.cpp" "$reference/codec.cpp")
clang -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-host.o"
clang++ "${flags[@]}" "${sources[@]}" "$out/lz4-host.o" -lvulkan -o "$out/host-image-copy-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang" -O2 -c "$lz/vendor/lz4.c" -o "$out/lz4-android.o"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" "$out/lz4-android.o" -static-libstdc++ -lvulkan -o "$out/host-image-copy-android"

clang++ "${flags[@]}" -fsanitize=address,undefined -fno-omit-frame-pointer "$experiment/gate_test.cpp" -o "$out/gate-test"
clang -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -c "$lz/vendor/lz4.c" -o "$out/lz4-asan.o"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "${sources[@]}" "$out/lz4-asan.o" -lvulkan -o "$out/host-image-copy-asan"
