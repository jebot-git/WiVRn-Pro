#!/usr/bin/env bash
set -euo pipefail
codec_here=$(cd -- "$(dirname -- "$0")" && pwd)
codec_sdk=${ANDROID_HOME:-$HOME/Android/Sdk}
codec_java=${JAVA_HOME:?Set JAVA_HOME to a JDK with javac (17 or later)}
codec_out=${1:-$codec_here/build}
mkdir -p "$codec_out/classes" "$codec_out/dex"
"$codec_java/bin/javac" -source 8 -target 8 -bootclasspath "$codec_sdk/platforms/android-36/android.jar" \
    -d "$codec_out/classes" "$codec_here/CodecHarness.java"
"$codec_java/bin/jar" cf "$codec_out/classes.jar" -C "$codec_out/classes" .
"$codec_sdk/build-tools/36.1.0/d8" --min-api 29 --lib "$codec_sdk/platforms/android-36/android.jar" \
    --output "$codec_out/dex" "$codec_out/classes.jar"
"$codec_java/bin/jar" cf "$codec_out/codec-harness.jar" -C "$codec_out/dex" classes.dex
printf '%s\n' "$codec_out/codec-harness.jar"
