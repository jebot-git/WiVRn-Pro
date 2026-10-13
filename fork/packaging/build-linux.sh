#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
source_dir=$(cd "$(dirname "$0")/../.." && pwd)
build_dir=${1:?Usage: build-linux.sh BUILD_DIRECTORY [extra CMake arguments...]}
shift
cmake -S "$source_dir" -B "$build_dir" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX=/usr \
  -DCMAKE_CXX_FLAGS=-Wno-error=maybe-uninitialized \
  -DCMAKE_INSTALL_LIBDIR=lib -DCMAKE_INSTALL_LIBEXECDIR=libexec \
  -DPython3_EXECUTABLE=/usr/bin/python3 \
  -DWIVRN_BUILD_CLIENT=OFF -DWIVRN_BUILD_SERVER=ON \
  -DWIVRN_BUILD_SERVER_LIBRARY=ON -DWIVRN_BUILD_DASHBOARD=ON \
  -DWIVRN_BUILD_WIVRNCTL=OFF -DWIVRN_USE_NVENC=ON -DWIVRN_USE_VAAPI=ON \
  -DWIVRN_USE_QSV=ON -DWIVRN_USE_VULKAN_ENCODE=ON \
  -DWIVRN_USE_PIPEWIRE=ON -DWIVRN_FEATURE_STEAMVR_LIGHTHOUSE=ON \
  -DWIVRN_OPTIMIZE_SHADERS=OFF -DWIVRN_WERROR=ON \
  -DGIT_DESC=v26.9-pro1-portability "$@"
cmake --build "$build_dir" --target wivrn-server openxr_wivrn --parallel "${BUILD_JOBS:-4}"
