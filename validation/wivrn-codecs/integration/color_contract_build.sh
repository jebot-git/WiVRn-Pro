#!/usr/bin/env bash
# Build only. Does not run GPU work or deploy anything.
set -euo pipefail
source_root="${1:?usage: color_contract_build.sh SOURCE OUT}"
output_dir="${2:?usage: color_contract_build.sh SOURCE OUT}"
script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
shader_compiler="${GLSLC:-glslc}"
cpp_compiler="${CXX:-c++}"
mkdir -p "$output_dir"
"$shader_compiler" --target-env=vulkan1.1 -O -fshader-stage=vert -DVERT_SHADER=1 "$source_root/client/shaders/reprojection.glsl" -o "$output_dir/reprojection.vert.spv"
"$shader_compiler" --target-env=vulkan1.1 -O -fshader-stage=frag -DFRAG_SHADER=1 "$source_root/client/shaders/reprojection.glsl" -o "$output_dir/reprojection.frag.spv"
"$cpp_compiler" -std=c++20 -O2 -Wall -Wextra "$script_dir/color_contract_probe.cpp" -lvulkan -o "$output_dir/color_contract_probe"
