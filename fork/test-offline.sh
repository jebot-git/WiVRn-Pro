#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
python3 fork/check-policy.py
python3 fork/check-wayvr-source.py
python3 -m unittest discover -s fork/tests -p "test_*.py"
python3 -m unittest discover -s apps/mac-desktop -p "test_*.py"
mkdir -p build/native-tests
# The real pacer uses Monado headers and utility libraries from a server build.
# Keep the standalone policy suite usable before those dependencies are built.
if [[ -f build/server/_deps/monado-build/src/xrt/auxiliary/util/libaux_util.a ]]; then
  python3 fork/tests/app_pacer_test.py build/server
else
  echo "SKIP app pacer: configure/build the server, then run fork/tests/app_pacer_test.py BUILD_DIR"
fi
cxx=${CXX:-c++}
"$cxx" -std=c++23 -Wall -Wextra -Werror -Icommon fork/tests/audio_recovery_test.cpp -o build/native-tests/audio-recovery-test
build/native-tests/audio-recovery-test
"$cxx" -std=c++23 -Wall -Wextra -Werror -pthread -Iserver fork/tests/video_recovery_test.cpp -o build/native-tests/video-recovery-test
build/native-tests/video-recovery-test
"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/refresh_rate_test.cpp -o build/native-tests/refresh-rate-test
build/native-tests/refresh-rate-test
spdlog_flags=()
if pkg-config --exists spdlog; then
  read -r -a spdlog_flags <<< "$(pkg-config --cflags --libs spdlog)"
elif [[ -f build/android-deps/spdlog-src/include/spdlog/spdlog.h ]]; then
  spdlog_flags=(-isystem build/android-deps/spdlog-src/include)
else
  echo "Passthrough lifecycle test requires spdlog development headers or the Android dependency cache" >&2
  exit 1
fi
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers -pthread \
  -Iclient -Icommon -Iexternal fork/tests/passthrough_lifecycle_test.cpp client/xr/passthrough.cpp \
  "${spdlog_flags[@]}" -o build/native-tests/passthrough-lifecycle-test
build/native-tests/passthrough-lifecycle-test
python3 fork/tests/portal_health_test.py
"$cxx" -std=c++23 -Wall -Wextra -Werror -Icommon fork/tests/policy_test.cpp -o build/native-tests/policy-test
build/native-tests/policy-test
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers -pthread \
  -Icommon -Iserver/driver fork/tests/bridge_test.cpp \
  server/driver/spatial_bridge.cpp -o build/native-tests/bridge-test
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -Iclient fork/tests/anchor_manager_test.cpp client/anchor_manager.cpp -o build/native-tests/anchors-test
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -Iclient -Ifork/tests fork/tests/guided_capture_test.cpp -o build/native-tests/capture-test
"$cxx" -std=c++23 -O1 -Icommon -Iexternal fork/tests/wire_test.cpp common/crypto.cpp common/smp.cpp -lcrypto -o build/native-tests/wire-test
"$cxx" -std=c++23 -O1 -funsigned-char -Icommon -Iexternal fork/tests/wire_test.cpp common/crypto.cpp common/smp.cpp -lcrypto -o build/native-tests/wire-unsigned-char-test
build/native-tests/wire-test > build/native-tests/wire-host.txt
build/native-tests/wire-unsigned-char-test > build/native-tests/wire-arm-char.txt
diff -u build/native-tests/wire-host.txt build/native-tests/wire-arm-char.txt
cat build/native-tests/wire-host.txt
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -Iclient -Icommon fork/tests/spatial_source_test.cpp client/spatial_source.cpp client/anchor_manager.cpp -o build/native-tests/spatial-source-test
build/native-tests/spatial-source-test
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Iclient -Icommon fork/tests/room_calibration_test.cpp -o build/native-tests/room-calibration-test
build/native-tests/room-calibration-test
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Iclient -Icommon fork/tests/passthrough_cutout_test.cpp -o build/native-tests/passthrough-cutout-test
build/native-tests/passthrough-cutout-test
build/native-tests/anchors-test
build/native-tests/capture-test
build/native-tests/bridge-test

"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/config_migration_test.cpp -o build/native-tests/config-migration-test
build/native-tests/config-migration-test
"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver -Icommon fork/tests/tracking_supervisor_test.cpp server/tracking_worker.cpp $(pkg-config --cflags --libs glib-2.0) -o build/native-tests/tracking-supervisor-test
build/native-tests/tracking-supervisor-test

"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/wayvr_supervisor_test.cpp server/wayvr_worker.cpp $(pkg-config --cflags --libs gio-2.0) -o build/native-tests/wayvr-supervisor-test
build/native-tests/wayvr-supervisor-test

"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/dome_supervisor_test.cpp server/dome_supervisor.cpp $(pkg-config --cflags --libs gio-2.0) -o build/native-tests/dome-supervisor-test
build/native-tests/dome-supervisor-test

"$cxx" -std=c++23 -Wall -Wextra -Werror -UNDEBUG -pthread -Iserver \
  fork/tests/session_policy_test.cpp $(pkg-config --cflags --libs gio-2.0) -o build/native-tests/session-policy-test
build/native-tests/session-policy-test

"$cxx" -std=c++23 -Wall -Wextra -Werror -fno-fast-math -Iserver/driver \
  fork/tests/hand_joystick_test.cpp server/driver/hand_joystick.cpp -o build/native-tests/hand-joystick-test
build/native-tests/hand-joystick-test
"$cxx" -std=c++23 -Wall -Wextra -Werror -fno-fast-math -Iserver/driver \
  fork/tests/hand_controller_test.cpp -o build/native-tests/hand-controller-test
build/native-tests/hand-controller-test
"$cxx" -std=c++23 -Wall -Wextra -Werror -fno-fast-math -Iserver/driver \
  fork/tests/walking_input_test.cpp server/driver/hand_joystick.cpp -o build/native-tests/walking-input-test
build/native-tests/walking-input-test

python3 -m unittest discover -s apps/hands-frame/tests -p "test_*.py"

"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/xrizer_discovery_test.cpp -o build/native-tests/xrizer-discovery-test
build/native-tests/xrizer-discovery-test
"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/cqp_rate_control_test.cpp $(pkg-config --cflags --libs libavcodec libavutil) -o build/native-tests/cqp-rate-control-test
build/native-tests/cqp-rate-control-test
python3 fork/check-motoc-source.py

"$cxx" -std=c++23 -Wall -Wextra -Werror -UNDEBUG -Iserver \
  fork/tests/pyrowave_foveation_test.cpp -o build/native-tests/pyrowave-foveation-test
build/native-tests/pyrowave-foveation-test

"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -pthread -Iserver -Icommon -Iexternal \
  fork/tests/hands_frame_transport_test.cpp common/crypto.cpp common/smp.cpp -lcrypto -o build/native-tests/hands-frame-transport-test
build/native-tests/hands-frame-transport-test
