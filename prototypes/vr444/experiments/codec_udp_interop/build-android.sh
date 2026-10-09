#!/usr/bin/env bash
set -euo pipefail
root=$(cd "$(dirname "$0")/../../../.." && pwd)
exp="$root/prototypes/vr444/experiments/codec_udp_interop"
lz="$root/prototypes/vr444/experiments/native_lz4"
ref="$root/validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444"
out=${1:-/tmp/wivrn-codec-udp-interop-build}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++"
c_compiler="$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang"
test -x "$compiler"
mkdir -p "$out"
flags=(-std=c++20 -O3 -DNDEBUG -Wall -Wextra -Werror -Wno-missing-field-initializers -static-libstdc++ -I "$lz" -I "$ref")
"$c_compiler" -O3 -DNDEBUG -c "$lz/vendor/lz4.c" -o "$out/lz4-android.o"
"$compiler" "${flags[@]}" "$exp/relay.cpp" -o "$out/relay-android"
"$compiler" "${flags[@]}" "$exp/decode.cpp" "$ref/codec.cpp" "$out/lz4-android.o" -o "$out/decode-android"
printf '%s\n' "$out/relay-android" "$out/decode-android (cross-compiled only; no device run)"
