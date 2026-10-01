#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p build/native-tests
cxx=${CXX:-c++}
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers -pthread \
  -Iwivrn/common -Iwivrn/server/driver native/tests/bridge_test.cpp \
  wivrn/server/driver/spatial_bridge.cpp -o build/native-tests/bridge-test
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -Iwivrn/client tests/anchor_manager_test.cpp wivrn/client/anchor_manager.cpp -o build/native-tests/anchors-test
"$cxx" -std=c++23 -O1 -Wall -Wextra -Werror -Wno-missing-field-initializers \
  -Iwivrn/client -Iquest/native tests/guided_capture_test.cpp -o build/native-tests/capture-test
"$cxx" -std=c++23 -O1 -Iwivrn/common -Iwivrn/external native/tests/wire_test.cpp wivrn/common/crypto.cpp wivrn/common/smp.cpp -lcrypto -o build/native-tests/wire-test
"$cxx" -std=c++23 -O1 -funsigned-char -Iwivrn/common -Iwivrn/external native/tests/wire_test.cpp wivrn/common/crypto.cpp wivrn/common/smp.cpp -lcrypto -o build/native-tests/wire-unsigned-char-test
build/native-tests/wire-test > build/native-tests/wire-host.txt
build/native-tests/wire-unsigned-char-test > build/native-tests/wire-arm-char.txt
diff -u build/native-tests/wire-host.txt build/native-tests/wire-arm-char.txt
cat build/native-tests/wire-host.txt
build/native-tests/anchors-test
build/native-tests/capture-test
build/native-tests/bridge-test
