#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
# Run in the Steam Runtime 4 ARM64 SDK. Cross builds may supply a CMake toolchain.
build_dir=${WIVRN_FRAME_BUILD_DIR:-build/frame}
cmake --preset frame -B "$build_dir" "$@"
cmake --build "$build_dir" --parallel "${BUILD_JOBS:-4}"
if [[ -n ${DESTDIR:-} ]]; then
    cmake --install "$build_dir"
    install -m755 fork/packaging/launch-wivrn-pro "$DESTDIR/launch-wivrn-pro"
    install -m644 fork/STEAM-FRAME.md "$DESTDIR/README.md"
fi
