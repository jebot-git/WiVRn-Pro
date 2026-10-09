VR444 research prototype — 2026-10-08
====================================

UPDATE: EXPANSION.txt describes the newer v2 dynamic-foveation implementation,
cooperative GPU kernels and hardware profile. The report below preserves the
original v1 measurements; its uniform-quantizer/GPU limitations are historical
where superseded there. HEVC carrier 0.1.0 is frozen in ../../testing-builds/.

Recommendation
--------------
Develop a WiVRn-specific, deadline-aware 4:4:4 streaming profile around PyroWave
before committing to a new transform/entropy codec. Use the included independent
VR444-Lift implementation to validate the portable GPU path, bitstream handling
and measurement harness. It is NOT a PyroWave implementation or a production
replacement. Its present compression results reject it for fine-detail VR at
50–150 Mbit/s. No Arc A770 or Quest Pro hardware timings were measured here.

Also investigate the included RGB-in-luma HEVC carrier. It retains full RGB
sampling while allowing standard HEVC420 hardware to carry the data. This is a
custom pixel layout around HEVC, not native HEVC444 support. It is a plausible
lower-risk path to the existing QSV/MediaCodec engines, conditional on validating
carrier dimensions, throughput and direct luma access on Quest Pro.

Research findings
-----------------
PyroWave is MIT-licensed, intra-only, supports YCbCr420 and 444, uses CDF9/7
wavelets, and can cap encoded image size. Its author targets high-bandwidth LAN
streaming. Published sub-millisecond desktop figures are not Arc/Quest results.
Source: https://github.com/Themaister/pyrowave

The inspected WiVRn proto/pyrowave tree is
a0f5700d0bae9bbe72876a59c78a385930c5c70d. Both host and client wrappers explicitly
select Chroma420; client images use G8B8R8_3PLANE_420. The decoder library itself
has 444 paths, including a fragment iDWT path. Integration therefore needs real
full-resolution chroma inputs, allocations, views, negotiation and presentation
changes, rather than only a codec dropdown option.
https://github.com/WiVRn/WiVRn/blob/proto/pyrowave/server/encoder/video_encoder_pyrowave.cpp
https://github.com/WiVRn/WiVRn/blob/proto/pyrowave/client/decoder/pyrowave/decoder.cpp
https://github.com/WiVRn/WiVRn/blob/proto/pyrowave/common/pyrowave/pyrowave_decoder.cpp

Current upstream PyroWave APIs require subgroup operations/size-control, and
encoder integer/storage features; FP16 is an optional optimization. Query the
actual headset driver, and run the upstream device-validation utility before
assuming compatibility. Fragment transforms do not automatically remove all
subgroup requirements from the remaining decoder stages.
https://github.com/Themaister/pyrowave/blob/master/pyrowave.h
https://github.com/Themaister/pyrowave/blob/master/pyrowave_device_validation.cpp

CreoleLink appears to have become CreoleCast. The official CreoleVR website calls
the streaming technology proprietary, advertises Quest Pro eye-tracked foveation,
and lists Windows as the PC platform. No reusable codec source or technical
bitstream specification was found in its public repositories. Treat it as a
design reference, not a code base or a verified latency benchmark. The unrelated
daniel-freiermuth/Creolecast repository is an Android audio-streaming application.
https://github.com/CreoleVR/creolevr.github.io/blob/main/index.html
https://github.com/CreoleVR/creolevr.github.io/blob/main/licenses.html

Production profile proposed: PyroWave444 + VR deadlines
------------------------------------------------------
1. Take the rendered RGB image BEFORE the existing NV12/420 conversion. Keep
   WiVRn's spatial foveation, but generate three full-resolution Y/Cb/Cr planes.
   Upsampling an existing 420 stream does not restore 444 information.
2. Encode independent intra pictures, with an explicit per-frame byte ceiling.
   Use a small number of independently schedulable spatial regions. Protect the
   gaze region first; spend remaining bits on peripheral coefficients. Avoid
   16x16 network packets: header overhead would overwhelm the benefit.
3. Send regions as soon as their encode/readback finishes. Bound outstanding
   work to the useful display deadline. Do not accumulate older frames to fill
   a bitrate target. Include frame, eye, region and pose/time identifiers.
4. On XR2+ Gen1, compare Vulkan compute against the available fragment iDWT path
   under sustained compositor load. Decode directly to GPU images, combine
   colour conversion/defoveation where practical, and retain late head-pose warp.
5. Select a coherent stereo frame at the deadline. Reproject the prior complete
   frame if necessary; never combine arbitrary left/right frame IDs. Foveal
   priority must not leave indefinitely stale peripheral tiles. FEC/retransmit
   choices must fit the remaining deadline; they are not implemented here.
6. Keep HEVC fallback during negotiation. Do not enable the new codec unless
   both capability checks and sustained target-device measurements pass.

Arc path: GLSL -> SPIR-V -> Intel ANV Vulkan compute, sharing the renderer's GPU
resources and explicit synchronization. A custom compute codec does not use
Intel iHD, QSV, oneVPL or the fixed-function HEVC encoder. The HEVC carrier DOES
use that existing QSV/iHD stack. Benchmark game+encoder contention, not idle GPU
throughput alone.

Quest path: Android NDK arm64 + Vulkan + the existing OpenXR renderer. This does
not install a new codec into Qualcomm's fixed-function decoder. Do not assume
QNN/Hexagon tooling exposes programmable video decoding. The carrier alternative
uses MediaCodec HEVC and GPU reconstruction instead. Android supports flexible
YUV image access, but a CPU ByteBuffer path is a diagnostic fallback, not the
desired low-copy presentation path. Validate AHardwareBuffer formats, usage,
plane views, range and acquire/release synchronization on the actual device.
https://developer.android.com/reference/android/media/MediaCodec
https://docs.vulkan.org/guide/latest/formats.html

Implemented prototype
---------------------
codec.cpp / codec.comp:
  * RGB8 -> reversible YCoCg-R, without spatial chroma subsampling.
  * Independent 16x16 tiles, four-level integer lifting Haar transform.
  * Unquantized DC; uniform AC quantization q=1..1024. q=1 is RGB lossless.
  * Groups of up to 16 coefficients: zero flag, bit width, repeated/dense/sparse
    mode, optional significance mask, zigzag signed values. No arithmetic coder.
  * Matching C++ and Vulkan encode/decode, including standalone GPU receive.
  * Explicit little-endian version-1 framing and bounded input parsing.
  * No temporal dependencies, B frames or lookahead in this codec.

VR44 frame format (experimental, not interoperable with PyroWave):
  six uint32 LE fields: magic 0x34345256, version 1, width, height, q, tile count;
  each raster tile: uint16 LE word count, followed by that many uint32 LE words.
  Bit order inside words is least-significant first. Each component begins with
  an 11-bit zigzag DC, then groups covering coefficient indices 1..255 in raster
  transform storage order. Nonzero group: 4-bit width (1..11), 2-bit mode
  (0=repeat, 1=dense, 2=sparse), 16-bit mask for sparse, then coefficient values.
  Trailing word padding must be zero. Edge pixels are replicated before transform.
  R/G/B inputs are 8-bit code values; alpha is ignored and decoded as opaque.

GPU harness limitations:
  One invocation per tile, large private arrays, serial bit packing, fixed
  1280-byte scratch slots/tile, coherent mapped buffers, and a fence-wait between
  calls. Host compaction is required for network use. It recreates Vulkan objects
  on each call. This deliberately simple baseline is not an optimized GPU kernel.
  Standalone GPU decode first performs full CPU validation/reconstruction for
  checking; that cost is excluded from dispatch timestamps. Production needs a
  cheaper validated packet path. No zero-copy renderer or OpenXR integration.

transport.py:
  Executable packetization/reassembly model, 1200-byte UDP payload, 32-byte
  application header, CRC32, at most one pending stereo frame. Handles reordering,
  duplicates, corruption, expiry and older-frame rejection. Clock-domain
  conversion and authenticated sessions are assumed external. It reassembles
  complete eyes before delivery; incremental region decoding is PROPOSED, not
  implemented. This is not a socket service or a WiVRn wire-protocol change.

atlas.py:
  Packs stereo RGB8 as three vertical panels in the HEVC luma plane; U/V are 128.
  Two 576x640 eyes yield one 1152x1920 carrier (199.066 million luma pixels/sec
  at 90Hz). RGB sampling is 444 even though the carrier syntax is HEVC420.
  Read raw decoded luma, bypass ordinary YUV->RGB conversion, and reconstruct
  R/G/B from the three panels. Range remapping, scaling or tone mapping is invalid
  for this data. The full-range lossless ramp test verifies all 256 code values.
  Carrier panels are aligned at 640-pixel boundaries in the current experiment.
  HEVC inter prediction retains packet-loss dependencies; it cannot inherit the
  intra codec's arbitrary-frame/region drop policy without IDR/refresh handling.

Measured results and interpretation
-----------------------------------
Evidence: ../../evidence/vr444-20261008/
Sources and shaders build for Linux and Android arm64 API29, NDK r29 14206865.
The sandbox exposes no /dev/dri, and no Quest execution was performed. Vulkan
functional checks ran on llvmpipe (LLVM22.1.8), a CPU software implementation.
At 576x640 q16, one cold submission took about 497ms encode +253ms decode there.
These are SOFTWARE dispatch numbers, not estimates for Arc or Adreno.

Custom codec test: 576x640 per eye, 90Hz, two identical synthetic eyes, opaque,
no audio/alpha. Budgets below include modeled application+IPv4/UDP overhead,
but not Ethernet/Wi-Fi headers, retransmission, FEC or encryption. Offline search
over 20 q values selects the best RGB PSNR under the budget; this is not a live
rate controller. Six timed CPU samples after one warmup, per single eye. CSV
p95 is the maximum of those six samples, not a robust tail-latency estimate.

Synthetic coloured fine-line/HUD stress scene:
 target     chosen q    actual incl. overhead    RGB PSNR    CPU enc/dec median
  50 Mb/s       768          41.09 Mb/s             17.90 dB       5.52/2.58 ms
 100 Mb/s       256          86.54 Mb/s             19.25 dB       5.81/2.88 ms
 150 Mb/s       128         145.35 Mb/s             23.68 dB       6.22/3.18 ms

Smooth gradients yield ~39.7–42.6dB, but noise yields only ~10.8–11.1dB. The
fine-detail result is the more relevant warning for VR text. 444 sampling alone
does not guarantee chroma fidelity after heavy quantization. Do not ship this
Haar/group-coding implementation on the strength of its gradient scores.

HEVC carrier smoke test: 30 moving synthetic frames (only 0.333 seconds),
libx265 ultrafast/zerolatency, no B frames/lookahead, one-frame VBV. Requested
50/100/150 Mb/s targets produced ~11.98/12.15/12.43 Mb/s and ~48.53/49.18/50.59dB
RGB PSNR after correcting full-range signalling at input AND output. The short,
simple inter-coded sequence does not consume its bitrate budget; these are not
measurements at a sustained 50/100/150 Mb/s, nor QSV quality/latency measurements.
Exact commands/actual sizes are saved in atlas/atlas-results.json. Early runs
without explicit input range lost fidelity (~27.4dB); do not omit that flag.
Lossless software encode/decode reproduces every
RGB ramp value exactly; this verifies the carrier concept, not QSV lossless mode.

No direct quality comparison to the previous HEVC benchmarks is valid: their
source images and measurement path differ. No acceptable visual threshold has
been established on the headset. The atlas result is encouraging for a carrier
prototype, but temporal prediction and this easy synthetic content make it
unsuitable for forecasting real VR compression.

Bandwidth and motion-to-photon design budget
------------------------------------------
The prior live 75% WiVRn setting yielded 576x640 per eye through spatial warping;
it is not a 75% allocation of bitrate to a central slice. Preserve that geometry
for the initial hardware comparison. Foveal quantization/gaze control is not yet
implemented in this prototype. The original 3840x2160 total-stream baseline is
much harder and must not be conflated with the small warped stream.

Stereo 576x640x90 RGB8 is 1.5925 Gb/s raw. At 50/100/150 Mb/s its payload budgets
are 0.754/1.507/2.261 bits/pixel, compression 31.85/15.93/10.62:1 before overhead.
3840x2160x90 RGB8 is 17.9159 Gb/s: only 0.067/0.134/0.201 bits/pixel at those rates.
Do not expect a simple intra-only 444 codec to preserve arbitrary fine detail
at that full-resolution budget. A three-panel full-4K carrier is also too large
to assume one hardware decoder supports it; dimensions need renegotiation.

At 90Hz, network serialization alone for a full stereo frame is:
  stream rate       300 Mb/s effective goodput       600 Mb/s effective goodput
     50 Mb/s                  1.85 ms                           0.93 ms
    100 Mb/s                  3.70 ms                           1.85 ms
    150 Mb/s                  5.56 ms                           2.78 ms
Goodput is measured application throughput, not the Wi-Fi PHY connection rate.
Region pipelining can overlap work, but cannot eliminate serialization cost.

Proposed hardware acceptance targets, NOT predicted/measured performance:
  stereo encode p95 <=0.75ms; stereo decode p95 <=1.5ms; packing/upload <=0.5ms;
  no growing frame queue; p99 deadline misses measured during sustained VR load.
At 100 Mb/s and 300 Mb/s goodput, these targets sum to ~6.45ms before compositor,
game, tracking, warp, display scheduling, scanout and network variability. A
90Hz display period is 11.11ms: one missed deadline can erase codec savings.
This is not a motion-to-photon prediction. The previous ~51–57ms encode-to-
predicted-display telemetry cannot be reduced by merely subtracting codec time.

Measure one frame ID through render-ready, encode submit/start/end, first/last
packet, receive, decode submit/start/end, compositor submission and predicted
display. Correlate device clocks and record GPU contention/thermal state. Finish
with an external motion/photodiode or high-speed-camera measurement of photons;
OpenXR predictedDisplayTime is not an optical measurement.

Build and reproduce
-------------------
From this directory (Linux needs CMake, Ninja, C++17, Vulkan headers/lib, glslc):
  cmake -S . -B build -G Ninja -DCMAKE_BUILD_TYPE=Release
  cmake --build build
  ctest --test-dir build --output-on-failure
  build/vr444 --bench
  python3 -m unittest discover -s . -p 'test_*.py' -v
  python3 atlas.py build/atlas-results --frames 30

QSV carrier test prepared for the A770 host (NOT run in this sandbox):
  python3 atlas.py build/atlas-qsv --frames 900 --encoder hevc_qsv \
    --device /dev/dri/renderD129
Verify the render node still belongs to A770. This uses veryfast, async_depth=1,
no B/GPB frames, no lookahead, extbrc=0, low_delay_brc=1, a one-frame VBV and a
90-frame GOP. It uploads CPU-generated input for testing; production must pack
directly from renderer images. The script measures size/quality, not per-frame
encode latency. Record QSV submission/completion timestamps separately.

Tests need numpy and ffmpeg/libx265. CTest Vulkan test uses the selected ICD.
For software validation only:
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/lvp_icd.x86_64.json \
    build/vr444 --gpu-test build

On the real Arc host, select the Intel ICD and correct physical-device index:
  VK_ICD_FILENAMES=/usr/share/vulkan/icd.d/intel_icd.x86_64.json \
    VR444_DEVICE=0 build/vr444 --gpu-bench build
Check the printed device name; an iGPU may be index 0. This harness performs a
single cold dispatch, not a representative p95 benchmark. Extend it with persistent
GPU resources, warmup and sustained concurrent rendering before evaluating speed.

Android cross-build:
  cmake -S . -B build-android -G Ninja -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_TOOLCHAIN_FILE=/home/blux/Android/Sdk/ndk/29.0.14206865/build/cmake/android.toolchain.cmake \
    -DANDROID_ABI=arm64-v8a -DANDROID_PLATFORM=android-29 -DANDROID_STL=c++_static
  cmake --build build-android

On an attached developer-enabled Quest (not performed in this task):
  adb shell mkdir -p /data/local/tmp/vr444
  adb push build-android/vr444 build-android/encode.spv build-android/decode.spv /data/local/tmp/vr444/
  adb shell chmod 755 /data/local/tmp/vr444/vr444
  adb shell /data/local/tmp/vr444/vr444 --self-test
  adb shell /data/local/tmp/vr444/vr444 --gpu-test /data/local/tmp/vr444
  adb shell /data/local/tmp/vr444/vr444 --gpu-bench /data/local/tmp/vr444
No APK, Meta upload or installed service is needed for this shell test.

Actual image files (RGBA bytes in R,G,B,A order; alpha ignored):
  build/vr444 --encode input.rgba 576 640 1 --out image.vr44
  build/vr444 --decode image.vr44 output.rgba
  build/vr444 --gpu-decode image.vr44 build output-gpu.rgba
The same .vr44 file can be transferred to Android for a host->Quest decode test.

Validation and remaining work
-----------------------------
Passed: exact lossless RGB, one-pixel colours, odd/partial tiles, multiple lossy
quantizers, CPU/GPU bitstream equivalence, independent GPU receive, serialized
file roundtrip, every truncation of a small frame, 2000 bounded mutations, eight
Python tests (packet deadlines and HEVC lossless carrier), Linux and Android build.
Clang AddressSanitizer+UndefinedBehaviorSanitizer reference tests passed. GCC's
ASan runtime was missing, so that compiler's sanitizer configuration was replaced
by Clang; no packages were installed. See evidence logs for toolchain details.

No headset run, Arc timing, PyroWave build/benchmark, QSV carrier benchmark,
renderer integration, region-level transport, gaze allocation or optical latency
measurement has been completed. Shell GitHub cloning was unavailable; relevant
upstream source was inspected through the GitHub connector. No upstream code was
vendored. All prototype source in this directory is original MIT-licensed code
under the repository's LICENSE-MIT. Live WiVRn and the Meta client are unchanged.
