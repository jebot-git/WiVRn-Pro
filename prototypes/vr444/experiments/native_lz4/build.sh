#!/usr/bin/env bash
set -euo pipefail
experiment=$(cd -- "$(dirname -- "$0")" && pwd)
output=${1:?Pass output directory}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
android="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin"
mkdir -p "$output"
clang -O3 -c "$experiment/vendor/lz4.c" -o "$output/lz4-host.o"
clang++ -std=c++20 -O3 "$experiment/probe.cpp" "$output/lz4-host.o" -o "$output/wpc-host"
clang -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer -c "$experiment/vendor/lz4.c" -o "$output/lz4-asan.o"
clang++ -std=c++20 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$experiment/probe.cpp" "$output/lz4-asan.o" -o "$output/wpc-asan"
"$android/aarch64-linux-android29-clang" -O3 -c "$experiment/vendor/lz4.c" -o "$output/lz4-android.o"
"$android/aarch64-linux-android29-clang++" -std=c++20 -O3 -static-libstdc++ "$experiment/probe.cpp" "$output/lz4-android.o" -o "$output/wpc-android-table"
"$android/aarch64-linux-android29-clang++" -std=c++20 -O3 -DWPC_ARM_CRC -static-libstdc++ "$experiment/probe.cpp" "$output/lz4-android.o" -o "$output/wpc-android-crc"
