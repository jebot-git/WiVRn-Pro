#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
# Requires JDK 17 and the Android SDK/NDK versions in build.gradle.
# Use WIVRN_BUILD_TASK=assembleRelease for a store build; see meta/README.md.
./gradlew --no-daemon --max-workers="${BUILD_JOBS:-4}" "${WIVRN_BUILD_TASK:-assembleDebug}" \
  -Psuffix=.spatial '-Pwivrn_app_name=WiVRn Pro' -Pwivrn_version=26.9-pro1 "$@"
