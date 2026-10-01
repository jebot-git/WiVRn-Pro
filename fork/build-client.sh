#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
# Requires JDK 21 and the Android SDK/NDK versions in build.gradle.
./gradlew --no-daemon --max-workers="${BUILD_JOBS:-4}" assembleDebug \
  -Psuffix=.spatial '-Pwivrn_app_name=WiVRn Spatial' -Pwivrn_version=26.9-spatial1 "$@"
