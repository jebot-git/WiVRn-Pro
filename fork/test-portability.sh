#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
python3 fork/check-motoc-source.py
mkdir -p build/native-tests
cxx=${CXX:-c++}
"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/xrizer_discovery_test.cpp -o build/native-tests/xrizer-discovery-test
build/native-tests/xrizer-discovery-test
"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver fork/tests/cqp_rate_control_test.cpp \
    $(pkg-config --cflags --libs libavcodec libavutil) -o build/native-tests/cqp-rate-control-test
build/native-tests/cqp-rate-control-test
monado_headers=''
for source in "${WIVRN_MONADO_SOURCE_DIR:-}" build/package-deps/monado-src build/server/_deps/monado-src; do
    if [[ -f "$source/src/xrt/include/xrt/xrt_defines.h" ]]; then
        monado_headers="$source/src/xrt/include"
        break
    fi
done
if [[ -z "$monado_headers" ]]; then
    echo 'Configure the server first, or set WIVRN_MONADO_SOURCE_DIR to its Monado source directory.' >&2
    exit 1
fi
"$cxx" -std=c++23 -Wall -Wextra -Werror -Iserver/driver -I"$monado_headers" \
    fork/tests/controller_roles_test.cpp -o build/native-tests/controller-roles-test
build/native-tests/controller-roles-test
python3 -m unittest discover -s fork/tests -p 'test_portability_dashboard.py'
cargo test --workspace --locked --manifest-path server/shora/Cargo.toml
CARGO_TARGET_DIR="$PWD/build/motoc-tests" cargo test --locked --manifest-path server/motoc/upstream/Cargo.toml
# Includes actual SlimeVR/Motoc wrist-layout parsing and dispatch/restart tests.
CARGO_TARGET_DIR="$PWD/build/wayvr-tests" cargo test --locked --release \
    --manifest-path server/wayvr/upstream/Cargo.toml -p wayvr --no-default-features --features openxr,wayland,x11
