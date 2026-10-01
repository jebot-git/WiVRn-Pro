#!/usr/bin/env bash
set -euo pipefail
bridge_root="$(cd -- "$(dirname -- "$0")" && pwd)"
mkdir -p "$bridge_root/build"
exec g++ -std=c++20 -O2 -Wall -Wextra -Werror -Wno-missing-field-initializers \
    "$bridge_root/tracking_source.cpp" -lopenxr_loader -o "$bridge_root/build/tracking-source"
