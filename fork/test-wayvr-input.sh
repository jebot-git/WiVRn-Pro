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
if [[ -n "${WIVRN_MONADO_SOURCE_DIR:-}" ]]; then
    "${CXX:-c++}" -std=c++23 "${input_cxxflags[@]}" -UNDEBUG -Wall -Wextra -Werror \
        -ffast-math -Iserver/driver -I"$WIVRN_MONADO_SOURCE_DIR/src/xrt/include" \
        fork/tests/held_pose_test.cpp -o build/native-tests/held-pose-test
    build/native-tests/held-pose-test
    echo 'PASS frozen controller/hand poses, loss flags, zero velocities and reacquisition'
    if [[ -n "${WIVRN_MONADO_BUILD_DIR:-}" ]]; then
        read -r -a input_eigen_flags <<< "$(pkg-config --cflags eigen3)"
        "${CXX:-c++}" -std=c++23 "${input_cxxflags[@]}" -UNDEBUG -fno-fast-math \
            -Iserver -Icommon -Iexternal -I"$WIVRN_MONADO_BUILD_DIR/../../common" "${input_eigen_flags[@]}" \
            -I"$WIVRN_MONADO_SOURCE_DIR/src/xrt/include" -I"$WIVRN_MONADO_SOURCE_DIR/src/xrt/auxiliary" \
            fork/tests/pose_hold_history_test.cpp server/driver/pose_list.cpp common/crypto.cpp common/smp.cpp \
            "$WIVRN_MONADO_BUILD_DIR/src/xrt/auxiliary/math/libaux_math.a" \
            -lcrypto -o build/native-tests/pose-hold-history-test
        build/native-tests/pose-hold-history-test
        echo 'PASS production pose history preserves position on loss/idle/expiry and resumes tracking'
    fi
fi
cd server/wayvr/upstream
for filter in mouse_priority hand_joystick pointer_pose backend::wayvr::input_capture; do
    cargo test --locked --release -p wayvr --no-default-features \
        --features openxr,wayland,x11 "$filter"
done
echo 'PASS WayVR nonblocking mouse monitoring, synthetic filtering, D-pad and capture routing'
