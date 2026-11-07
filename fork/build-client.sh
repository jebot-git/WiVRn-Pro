#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
set -euo pipefail
cd "$(dirname "$0")/.."
# Requires JDK 17 and the Android SDK/NDK versions in build.gradle.
# Signing material is supplied through WIVRN_SIGNING_STORE/PASSWORD outside the tree.
read -r release_version release_code < <(python3 -c 'import json; r=json.load(open("fork/release.json")); print(r["version"], r["android_version_code"])')
./gradlew --no-daemon --max-workers="${BUILD_JOBS:-4}" "${WIVRN_BUILD_TASK:-assembleRelease}" \
  -Psuffix=.spatial '-Pwivrn_app_name=WiVRn Pro' -Pwivrn_version="${release_version}" -Pwivrn_versionCode="${release_code}" -Pwivrn_pyrowave=1 "$@"
