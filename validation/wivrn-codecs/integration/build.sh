#!/usr/bin/env bash
set -euo pipefail
codec_source=$(realpath "${1:?Pass isolated patched WiVRn source directory}")
codec_sdk=${ANDROID_HOME:-$HOME/Android/Sdk}
codec_aux=${WIVRN_AUX_TOOLCHAIN:-/home/blux/kwin/spatial-anchoring/wivrn-upstream/toolchain}
export PATH="$codec_sdk/ndk/29.0.14206865/shader-tools/linux-x86_64:$codec_aux/KTX-Software-4.4.2-Linux-x86_64/bin:$codec_aux/usr/bin:$PATH"
: "${JAVA_HOME:?Set JAVA_HOME to a JDK 17 with javac}"
export ANDROID_HOME="$codec_sdk" CMAKE_BUILD_PARALLEL_LEVEL=4
# The source snapshot was configured with its original cached Monado/Android deps.
# This script deliberately uses that isolated cache, never the production build.
test -f "$codec_source/build/codec-server/CMakeCache.txt"
for codec_stage in encode decode; do
  codec_define=${codec_stage^^}
  glslc --target-env=vulkan1.1 -O -D"$codec_define"=1 \
    "$codec_source/common/codec_test/vr444/shaders/codec.comp" -o "$codec_source/assets/codec-test/$codec_stage.spv"
  for codec_group in 64 128; do
    glslc --target-env=vulkan1.1 -O -D"$codec_define"=1 -DWORKGROUP="$codec_group" \
      "$codec_source/common/codec_test/vr444/shaders/codec_coop.comp" -o "$codec_source/assets/codec-test/$codec_stage-coop$codec_group.spv"
  done
done
glslc --target-env=vulkan1.1 -O -DDECODE=1 -DWORKGROUP=128 \
  "$codec_source/common/codec_test/vr444/shaders/constant_decode.comp" -o "$codec_source/assets/codec-test/decode-coop128dc.spv"
cmake --build "$codec_source/build/codec-server" --target wivrn-server openxr_wivrn monado -j4
"$codec_source/gradlew" -p "$codec_source" assembleDebug --offline --no-daemon --max-workers=4 \
 '-Pwivrn_app_name=WiVRn Codec Tests' -Pwivrn_version=26.9-codec-test1 -Pwivrn_offline=1 \
 -Pwivrn_codec_tests=1 "-Pfetchcontent_base_dir=$codec_source/build/android-deps"
python3 - "$codec_source" <<'PY'
import json,sys
from pathlib import Path
p=Path(sys.argv[1])/'build/android/outputs/apk/debug/output-metadata.json'
assert json.loads(p.read_text())['applicationId']=='org.meumeu.wivrn.codectest', 'refuse production package'
print(p.parent/'wivrn-codec-tests-debug.apk')
PY
