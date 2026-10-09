#!/usr/bin/env bash
set -euo pipefail
project_root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$project_root/prototypes/vr444/experiments/compact_gpu"
intra="$project_root/prototypes/vr444/experiments/intra_v2"
reference="$project_root/.worktrees/wivrn-codec-tests/common/codec_test/vr444"
output=${1:-"$project_root/validation/results/codec-viability-0.8.0/compact/build"}
profile=${2:-scalar}
case "$profile" in
    scalar) shader_source="$experiment/compact_decode.comp"; inverse_label=scalar-lane-zero ;;
    unconditional) shader_source="$experiment/compact_unconditional.comp"; inverse_label=cooperative-unconditional ;;
    *) echo "Unknown profile: $profile" >&2; exit 2 ;;
esac
ndk=${ANDROID_NDK_HOME:-/home/blux/Android/Sdk/ndk/29.0.14206865}
mkdir -p "$output/shaders"
flags=("-DCOMPACT_LEGACY_LABEL=\"$inverse_label\"" -std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers -I "$reference" -I "$intra")
sources=("$experiment/probe.cpp" "$experiment/compact.cpp" "$intra/intra_v2.cpp" "$reference/codec.cpp" "$reference/vulkan_runner.cpp")
clang++ "${flags[@]}" "${sources[@]}" -lvulkan -o "$output/compact_gpu_probe"
"$ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" "${flags[@]}" "${sources[@]}" -static-libstdc++ -lvulkan -o "$output/compact_gpu_probe_android"
clang++ "${flags[@]}" "$experiment/make_corpus.cpp" "$intra/intra_v2.cpp" "$reference/codec.cpp" -o "$output/make_corpus"
"$ndk/shader-tools/linux-x86_64/glslc" --target-env=vulkan1.1 -O "$shader_source" -o "$output/shaders/compact-decode.spv"
for stage in encode decode; do
    define=${stage^^}
    "$ndk/shader-tools/linux-x86_64/glslc" --target-env=vulkan1.1 -O -D"$define"=1 "$reference/shaders/codec.comp" -o "$output/shaders/$stage.spv"
    for group in 64 128; do
        "$ndk/shader-tools/linux-x86_64/glslc" --target-env=vulkan1.1 -O -D"$define"=1 -DWORKGROUP="$group" "$reference/shaders/codec_coop.comp" -o "$output/shaders/$stage-coop$group.spv"
    done
done
