#!/usr/bin/env bash
set -euo pipefail
here=$(cd -- "$(dirname -- "$0")" && pwd)
out=${1:?usage: build.sh ABSOLUTE_OUTPUT_DIRECTORY}
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$out/shaders"
flags=(-std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -Wno-misleading-indentation)
clang++ "${flags[@]}" "$here/replay.cpp" -lvulkan -o "$out/replay"
clang++ "${flags[@]}" -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$here/replay.cpp" -lvulkan -o "$out/replay-asan"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "$here/replay.cpp" -static-libstdc++ -lvulkan -o "$out/replay-android"
for stage in vert frag; do
    glslc --target-env=vulkan1.1 -O -fshader-stage="$stage" -D"${stage^^}_SHADER" "$here/reprojection.glsl" -o "$out/shaders/reprojection.$stage.spv"
    "$ndk/shader-tools/linux-x86_64/spirv-val" --target-env vulkan1.1 "$out/shaders/reprojection.$stage.spv"
done
