Color sampling regression (standalone offscreen graphics)

Build host, with the shader compiler supplied explicitly if needed:
  GLSLC=/path/to/glslc ./color_contract_build.sh /path/to/wivrn-codec-tests /path/to/output
Run only after reserving the GPU:
  /path/to/output/color_contract_probe /path/to/output/reprojection.vert.spv /path/to/output/reprojection.frag.spv 0

Android cross-build, using already generated shader SPIR-V:
  /home/blux/Android/Sdk/ndk/29.0.14206865/toolchains/llvm/prebuilt/linux-x86_64/bin/aarch64-linux-android29-clang++ -std=c++20 -O2 -static-libstdc++ color_contract_probe.cpp -lvulkan -o color_contract_probe_android
The binary takes the same two SPIR-V paths and optional physical-device index.
Build script does not deploy or run. Deployment/device runs belong to primary.

Predeclared pass tolerance: 2 stored 8-bit codes per RGBA channel, all pixels.
72 cases exercise the actual reprojection.glsl vertex/fragment shaders with
SRGB output attachment and fence/readback. Gray and primary ramps, hard edges,
varied colors, mirrored-eye fixture, center/quarter/half bilinear offsets,
identity and scale=.65/bias=.04 dimming. Input view is UNORM or SRGB.
Corrected test policy skips manual EOTF after hardware SRGB sampling. Legacy
policies preserve the existing headset flag. Old double-EOTF path is an explicit
negative control: it must match the old mathematical policy and differ from the
correct policy by at least 20 codes. Pipeline reuse is exercised after switches.

This is not the stream_defoveator C++ mesh/cache implementation, OpenXR runtime,
codec, external compositor, headset display or optical test. Policy is mirrored
in probe code; full APK build and live post-defoveation capture remain separate.
