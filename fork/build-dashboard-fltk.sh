#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Build only the optional native dashboard; does not install or restart anything.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
cmake -S "$root/dashboard" -B "${WIVRN_DASHBOARD_BUILD_DIR:-$root/build-dashboard-fltk}" -G Ninja \
  -DWIVRN_DASHBOARD_FRONTEND=fltk \
  -DCMAKE_PREFIX_PATH="${DOME_FLTK_PREFIX:-/opt/fltk-vr/toolkit}" \
  -DWIVRN_DOME_SOURCE="${DOME_SOURCE:-$HOME/de-xr/dome-compositor}" \
  -DCMAKE_BUILD_TYPE=Release "$@"
cmake --build "${WIVRN_DASHBOARD_BUILD_DIR:-$root/build-dashboard-fltk}" --parallel "${BUILD_JOBS:-4}"
