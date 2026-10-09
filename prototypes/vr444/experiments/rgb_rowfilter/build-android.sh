#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../../.." && pwd)
exp="$root/prototypes/vr444/experiments/rgb_rowfilter"
lz="$root/prototypes/vr444/experiments/native_lz4"
ref="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
direct="$root/prototypes/vr444/experiments/latency_decode"
out=${1:-/tmp/wivrn-rgb-rowfilter-simd-build}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++"
c_compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang"
test -x "$compiler"
mkdir -p "$out"
flags=(-std=c++20 -O3 -DNDEBUG -Wall -Wextra -Werror -Wno-missing-field-initializers -static-libstdc++ -I "$lz" -I "$ref" -I "$direct")
"$c_compiler" -O3 -DNDEBUG -c "$lz/vendor/lz4.c" -o "$out/lz4-android.o"
"$compiler" "${flags[@]}" -DRRF_COMPARE_SIMD "$exp/bench.cpp" "$ref/codec.cpp" "$direct/direct.cpp" "$out/lz4-android.o" -o "$out/compare-android"
"$compiler" "${flags[@]}" "$exp/test.cpp" "$out/lz4-android.o" -o "$out/test-android"
printf '%s\n' "$out/compare-android" "$out/test-android (cross-compiled only; no device run)"
