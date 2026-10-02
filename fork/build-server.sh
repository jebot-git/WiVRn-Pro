#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
# Extra arguments permit encoder choices, dependency caches, and install prefix.
cmake -S . -B build/server -G Ninja \
  -DWIVRN_BUILD_CLIENT=OFF -DWIVRN_BUILD_SERVER=ON \
  -DWIVRN_BUILD_SERVER_LIBRARY=ON -DWIVRN_BUILD_DASHBOARD=ON \
  -DWIVRN_BUILD_WIVRNCTL=OFF -DWIVRN_USE_VAAPI=ON \
  -DWIVRN_USE_PIPEWIRE=ON \
  -DWIVRN_USE_VULKAN_ENCODE=ON -DWIVRN_OPTIMIZE_SHADERS=OFF -DWIVRN_WERROR=ON \
  -DGIT_DESC=v26.9-pro1 -DGIT_COMMIT="$(git rev-parse HEAD)" "$@"
cmake --build build/server --target wivrn-server openxr_wivrn --parallel "${BUILD_JOBS:-6}"
