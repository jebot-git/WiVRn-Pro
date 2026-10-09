#!/usr/bin/env bash
set -euo pipefail
root=$(cd -- "$(dirname -- "$0")/../../../.." && pwd)
experiment="$root/prototypes/vr444/experiments/cpu_upload_live"
source_dir="$root/.worktrees/wivrn-codec-tests/common/codec_test/vr444"
output_dir=${1:-"$experiment/build"}
mkdir -p "$output_dir"
flags=(-std=c++20 -O1 -g -Wall -Wextra -Werror -Wno-missing-field-initializers -fsanitize=address,undefined -fno-omit-frame-pointer -I "$source_dir")
clang++ "${flags[@]}" "$experiment/destination_test.cpp" "$source_dir/direct.cpp" "$source_dir/codec.cpp" -o "$output_dir/destination-test"
"$output_dir/destination-test"
