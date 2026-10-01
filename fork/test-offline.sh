#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/native-tests
cxx=${CXX:-c++}
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
build/native-tests/anchors-test
build/native-tests/capture-test
build/native-tests/bridge-test
