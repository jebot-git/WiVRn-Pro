#!/usr/bin/env bash
set -euo pipefail
exp=$(cd "$(dirname "$0")" && pwd)
python3 "$exp/build.py" "${1:-/tmp/wivrn-codec-udp-interop-build}"
