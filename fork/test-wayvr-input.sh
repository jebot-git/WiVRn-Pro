#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/native-tests
read -r -a input_cxxflags <<< "${CXXFLAGS:--O2}"
"${CXX:-c++}" -std=c++23 "${input_cxxflags[@]}" -UNDEBUG -Wall -Wextra -Werror \
    -fno-fast-math -Iserver/driver fork/tests/hand_joystick_test.cpp \
    server/driver/hand_joystick.cpp -o build/native-tests/hand-joystick-test
build/native-tests/hand-joystick-test
echo 'PASS hand joystick profile stability, input expiry, physical takeover and disconnect'
cd server/wayvr/upstream
for filter in mouse_priority hand_joystick backend::wayvr::input_capture; do
    cargo test --locked --release -p wayvr --no-default-features \
        --features openxr,wayland,x11 "$filter"
done
echo 'PASS WayVR nonblocking mouse monitoring, synthetic filtering, D-pad and capture routing'
