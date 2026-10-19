#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
python3 -m unittest discover -s fork/tests -p test_hands_frame.py
cargo test --locked -p wayvr --no-default-features --features openxr,wayland,x11 \
    --manifest-path server/wayvr/upstream/Cargo.toml hands_frame
# Set WIVRN_MONADO_SOURCE_DIR to a configured/pinned Monado checkout.
if [[ -n "${WIVRN_MONADO_SOURCE_DIR:-}" ]]; then
    mkdir -p build/native-tests
    read -r -a hands_vulkan_flags <<< "$(pkg-config --cflags --libs vulkan)"
    "${CXX:-c++}" -std=c++23 -O1 -UNDEBUG -DVULKAN_HPP_NO_STRUCT_CONSTRUCTORS -pthread \
        -Iserver -Icommon -Iexternal -I"$WIVRN_MONADO_SOURCE_DIR/src/xrt/include" \
        -I"$WIVRN_MONADO_SOURCE_DIR/src/xrt/auxiliary" \
        fork/tests/hands_frame_readback_test.cpp server/compositor/hands_frame_capture.cpp \
        common/vk/allocation.cpp common/vk/vk_allocator.cpp common/vk/vk_mem_alloc.cpp common/vk/error_category.cpp \
        "${hands_vulkan_flags[@]}" -ldl -o build/native-tests/hands-frame-readback-test
    build/native-tests/hands-frame-readback-test
fi
