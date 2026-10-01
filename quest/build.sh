#!/usr/bin/env bash
set -euo pipefail
quest_root="$(cd -- "$(dirname -- "$0")" && pwd)"
quest_sdk="${ANDROID_HOME:-$HOME/Android/Sdk}"
quest_ndk="${ANDROID_NDK_HOME:-$quest_sdk/ndk/29.0.14206865}"
quest_java="${JAVA_HOME:?Set JAVA_HOME to a JDK 21 installation}"
quest_tools="$quest_sdk/build-tools/36.1.0"
quest_build="$quest_root/build"
quest_native="$quest_root/native"
quest_loader="${OPENXR_ANDROID_LOADER:-$quest_root/.deps/libopenxr_loader.so}"
quest_headers="${OPENXR_HEADERS:-$quest_root/.deps/include}"
test -f "$quest_loader" || { echo "Set OPENXR_ANDROID_LOADER to the arm64 Android OpenXR loader" >&2; exit 1; }
test -f "$quest_headers/openxr/openxr.h" || { echo "Set OPENXR_HEADERS to the OpenXR include directory" >&2; exit 1; }
mkdir -p "$quest_build/classes" "$quest_build/dex" "$quest_build/lib/arm64-v8a" "$quest_build/include"
# Header-only dependency: isolated include root avoids host libc headers in Android builds.
cp -r /usr/include/nlohmann "$quest_build/include/"
cp "$quest_loader" "$quest_build/lib/arm64-v8a/libopenxr_loader.so"
"$quest_ndk/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++" \
 -std=c++23 -O2 -shared -fPIC -static-libstdc++ -Wall -Wextra -Wno-missing-field-initializers \
 -I"$quest_build/include" -I"$quest_headers" \
 -I"$quest_native" \
 "$quest_root/source.cpp" "$quest_native/anchor_manager.cpp" \
 -L"$quest_build/lib/arm64-v8a" -lopenxr_loader -landroid -llog -lEGL -lGLESv3 \
 -o "$quest_build/lib/arm64-v8a/libquestsource.so"
"$quest_java/bin/javac" -source 8 -target 8 -bootclasspath "$quest_sdk/platforms/android-36/android.jar" \
 -d "$quest_build/classes" "$quest_root/src/org/vrmcompanion/roomsource/MainActivity.java"
"$quest_java/bin/jar" cf "$quest_build/classes.jar" -C "$quest_build/classes" .
JAVA_HOME="$quest_java" "$quest_tools/d8" --min-api 29 --output "$quest_build/dex" "$quest_build/classes.jar"
"$quest_tools/aapt2" link -I "$quest_sdk/platforms/android-36/android.jar" --manifest "$quest_root/AndroidManifest.xml" -o "$quest_build/unaligned.apk"
python3 - "$quest_build" <<'PY'
import pathlib,sys,zipfile
root=pathlib.Path(sys.argv[1])
with zipfile.ZipFile(root/'unaligned.apk','a',zipfile.ZIP_DEFLATED) as apk:
    apk.write(root/'dex/classes.dex','classes.dex')
    for lib in (root/'lib/arm64-v8a').glob('*.so'):
        apk.write(lib,'lib/arm64-v8a/'+lib.name)
PY
"$quest_tools/zipalign" -f -p 4 "$quest_build/unaligned.apk" "$quest_build/RoomSource.apk"
if [ ! -f "$quest_build/debug.keystore" ]; then
 "$quest_java/bin/keytool" -genkeypair -keystore "$quest_build/debug.keystore" -storepass android -keypass android \
 -alias androiddebugkey -dname 'CN=Room Source Prototype' -keyalg RSA -keysize 2048 -validity 10000
fi
JAVA_HOME="$quest_java" "$quest_tools/apksigner" sign --ks "$quest_build/debug.keystore" --ks-pass pass:android "$quest_build/RoomSource.apk"
printf 'Built %s\n' "$quest_build/RoomSource.apk"
