#!/usr/bin/env bash
set -euo pipefail
source_dir=$(cd -- "$(dirname -- "$0")" && pwd)
output=${1:?output directory required}
mkdir -p "$output"
clang++ -std=c++20 -O3 -Wall -Wextra -Wno-missing-field-initializers "$source_dir/probe.cpp" "$source_dir/baseline/codec.cpp" "$source_dir/baseline/vulkan_runner.cpp" -lvulkan -o "$output/probe"
clang++ -std=c++20 -O1 -g -fsanitize=address,undefined -fno-omit-frame-pointer "$source_dir/probe.cpp" "$source_dir/baseline/codec.cpp" "$source_dir/baseline/vulkan_runner.cpp" -lvulkan -o "$output/probe-sanitizer"
