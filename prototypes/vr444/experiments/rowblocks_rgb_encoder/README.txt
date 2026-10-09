Direct packed-RGB fused row-block encoder
========================================

Separate offline candidate derived from fused encoder355b3b1. It reconstructs
each independent VR444 tile band directly into packed R,G,B triples and feeds
those bytes to canonical RRF compression. It removes the intermediate RGBA band,
opaque-alpha scan and RGB repacking. Tile arithmetic, clipping, quantization,
wire version, RRF choice/tie rules, raw fallback and final RRB assembly stay exact.
There is no new wire format or production API change.

direct_rgb.cpp is an experimental output-store derivative of the MIT-licensed
latency_decode/direct.cpp. It delegates frame validation to the unchanged original
validator and retains its scalar/SSE2/NEON inverse wavelet and coefficient guards.
It writes three bounded bytes per pixel, with scalar cropped-tile stores. The
alpha channel is known opaque by the original decoder contract; no arbitrary
source alpha is discarded. Output remains private until successful return because
malformed later coefficients can follow earlier private writes.

encode_rgb.hpp accepts checked, exact-sized, immutable RGB24 source separate from
all compression workspace buffers. It applies the same compression attempts and
minimum64-byte gain rule in the same order as rowfilter::encode, including exact
header/body CRCs. It does not allocate the encoder's redundant RGB-copy buffer.
Both original and packed-source paths must produce identical canonical bytes.

The fused stereo API retains at most512 jobs,1..4 persistent workers and one
synchronous barrier. Each worker owns its source-band/RGB/compression scratch;
completed records occupy distinct block slots. Both eyes pass preflight and the
4Mi-pixel/axis4096 limit before any worker runs. Both complete canonical eyes are
assembled privately before a lease is returned. All workspace-owned input aliases
are rejected before mutation; later errors drain workers and clear both outputs.
Leases expire on next call/clear/destruction. External session/frame/deadline and
atomic publication admission remain separate caller responsibilities.

The frozen corpus contains prior VR444 bytes, not original framebuffers. Exactness
means preserving those decoded pixels, including prior Q8 loss. This does not make
Q8 lossless or establish the cost of encoding original server framebuffer data.

ASan+UBSan and ThreadSanitizer each pass733 canonical stereo cases,128 direct RGB
scalar/SIMD versus original pixel oracles,2000 differential corruption cases and
3660 rejection checks. Tests include canary-protected odd stores, separate eye
content/qmaps, quantizers1/8/64/1024, source/output overlap, wrong shapes/lengths,
late worker coefficient failure, all block sizes/policies,512 jobs/two4Mi-pixel
eyes and300 repeated batches. Independent review found no store/bounds/alias or
lease blocker. Android API29 binaries cross-compile only.

The final26-profile x12-arm x31-repetition observation retains the original fused
RGBA four-worker arm in the same rotated benchmark, alongside packed RGB serial/
two/four-worker arms for32/128-row blocks. Every RRB client remains the same four
workers. All9672 measured invocations pass canonical-wire and both-eye pixel
oracles. Complete matched server/client samples and source/corpus/binary hashes
are recorded. See RESULTS.txt: smaller scratch does not yield a consistent
latency improvement, so this candidate is not promoted.

The build searches latency_decode before the integration overlay for direct.hpp.
Those two headers are byte-identical at qualification, but explicit include order
avoids ambiguity and binds this candidate to the measured source dependencies.

Reproduce
---------
  RRB_RGB_TSAN=1 bash prototypes/vr444/experiments/rowblocks_rgb_encoder/build.sh
  /tmp/wivrn-rowblocks-rgb-encoder-build/test
  /tmp/wivrn-rowblocks-rgb-encoder-build/test-tsan
  bash prototypes/vr444/experiments/rowblocks_rgb_encoder/build-android.sh
  # Only after a coordinated CPU measurement window:
  python3 prototypes/vr444/experiments/rowblocks_rgb_encoder/measure.py \
    /path/to/compact/corpus /tmp/rowrgb

No headset, GPU, socket or production configuration operation is part of this
experiment. Android build is cross-compilation only; headset tests stay suspended.
