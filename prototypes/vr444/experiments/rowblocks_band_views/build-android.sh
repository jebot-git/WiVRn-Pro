#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../../.." && pwd)
exp="$root/prototypes/vr444/experiments/rowblocks_band_views"
lz="$root/prototypes/vr444/experiments/native_lz4"
ref="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
direct="$root/prototypes/vr444/experiments/latency_decode"
out=${1:-/tmp/wivrn-rowblocks-band-views-build}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++"
c_compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang"
test -x "$compiler"
mkdir -p "$out"
flags=(-std=c++20 -pthread -O3 -DNDEBUG -Wall -Wextra -Werror -Wno-missing-field-initializers -static-libstdc++ -I "$lz" -I "$direct" -I "$ref")
"$c_compiler" -O3 -DNDEBUG -c "$lz/vendor/lz4.c" -o "$out/lz4-android.o"
for target in test bench; do
    "$compiler" "${flags[@]}" "$exp/$target.cpp" "$ref/codec.cpp" "$direct/direct.cpp" "$exp/direct_rgb.cpp" "$exp/../rowblocks_rgb_encoder/direct_rgb.cpp" "$exp/../rowblocks_rgb_vector/direct_rgb.cpp" "$out/lz4-android.o" -o "$out/$target-android"
done
printf '%s\n' "$out/test-android" "$out/bench-android (cross-compiled only; no device run)"
