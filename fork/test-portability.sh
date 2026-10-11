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
python3 -m unittest discover -s fork/tests -p 'test_portability_dashboard.py'
cargo test --workspace --locked --manifest-path server/shora/Cargo.toml
CARGO_TARGET_DIR="$PWD/build/motoc-tests" cargo test --locked --manifest-path server/motoc/upstream/Cargo.toml
# Includes actual SlimeVR/Motoc wrist-layout parsing and dispatch/restart tests.
CARGO_TARGET_DIR="$PWD/build/wayvr-tests" cargo test --locked --release \
    --manifest-path server/wayvr/upstream/Cargo.toml -p wayvr --no-default-features --features openxr,wayland,x11
