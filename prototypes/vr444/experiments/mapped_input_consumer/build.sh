#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/mapped_input_consumer"
reference="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
lz="$root/prototypes/vr444/experiments/native_lz4"
out=${1:-"$experiment/build"}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
bash "$root/prototypes/vr444/experiments/mapped_input_decode/build.sh" "$out/base"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$lz")
sources=("$experiment/probe.cpp" "$reference/codec.cpp")
clang++ "${flags[@]}" "${sources[@]}" "$out/base/lz4-host.o" -lvulkan -o "$out/mapped-consumer-host"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" "$out/base/lz4-android.o" -static-libstdc++ -lvulkan -o "$out/mapped-consumer-android"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "${sources[@]}" "$out/base/lz4-asan.o" -lvulkan -o "$out/mapped-consumer-asan"
