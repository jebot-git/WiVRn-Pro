Vector color reconstruction and exact packed RGB stores
=======================================================

Separate offline follow-up to packed-RGB branch2066d45, whose scalar triple stores
did not consistently improve host latency. This candidate keeps that scalar-store
four-worker control and the earlier fused RGBA four-worker control in the same
rotated experiment. No RRF/RRB format, quality or client API changes.

The independent VR444 wavelet reconstruction remains unchanged. At each cropped
tile row, the new helper reconstructs four color pixels with SSE2 or eight with
NEON. Integer floor-half and YCoCg arithmetic equal the original scalar function.
SSE2 signed32→signed16 saturation followed by unsigned8 saturation equals scalar
clamp[0,255]; RG/BA unpacking forms four opaque pixels, then an SSSE3 shuffle stores
exactly8+4 RGB bytes. Runtime SSSE3 detection occurs before any such call. NEON
signed32→unsigned16→unsigned8 saturation forms eight channels, and vst3 stores
exactly24 bytes. Constant tiles use the same bounded four/eight-pixel stores.
Every remaining cropped pixel uses the original scalar conversion/store.

Loads require at least four/eight pixels remaining in the current <=16-pixel row.
Thus no coefficient load crosses a row or plane and no store crosses the cropped
output extent. Full preflight, exact output size and source/output overlap checks
precede reconstruction. Partial private output on later coefficient failure is
never published. The existing fused stereo layer owns scratch per worker, copies
each complete block before reuse, drains one bounded batch and assembles both
canonical eyes before returning a lease. Its existing alias/failure/lease and
4Mi-pixel, axis4096,512-job bounds remain unchanged.

The original encoder policy/order/tie/minimum64-byte-gain/raw fallback and CRCs
remain byte-exact. No arbitrary alpha is discarded: decoder output is inherently
opaque. Input is existing VR444 corpus data; output preserves its decoded pixels,
including prior Q8 loss. No original framebuffer encoding claim follows.

Independent read-only review checked saturation equivalence, RGB lane order,
runtime gating and all cropped-row load/store bounds; no blocker was found.
The direct RGB oracle covers every width1..15, full16-pixel rows, odd dimensions,
quantizers1/8/64/1024, scalar/vector paths and canaries. It is complemented by
canonical stereo comparisons,2000 differential mutations and the inherited
worker/alias/failure/max-dimension tests. Android NEON is cross-compiled only;
host x86 correctness does not claim ARM runtime execution.

Twelve benchmark arms retain VR444+optional WPC1 and global RRF-up plus, for each
32/128-row block size: prior packed scalar-store workers4, prior fused RGBA workers4,
and this candidate serial/workers2/workers4. All RRB clients are the same four
workers. Output oracles are outside the clock; paired transcode+decode samples,
stage CPU/wall times and source/corpus/binary hashes are retained. Backend reporting
distinguishes SSE2 inverse arithmetic from the SSSE3 store gate. No other GPU or
transport measurement is added to these values.

Reproduce
---------
  RRB_RGB_VECTOR_TSAN=1 bash prototypes/vr444/experiments/rowblocks_rgb_vector/build.sh
  /tmp/wivrn-rowblocks-rgb-vector-build/test
  /tmp/wivrn-rowblocks-rgb-vector-build/test-tsan
  bash prototypes/vr444/experiments/rowblocks_rgb_vector/build-android.sh
  # Only after a coordinated CPU measurement window:
  python3 prototypes/vr444/experiments/rowblocks_rgb_vector/measure.py \
    /path/to/compact/corpus /tmp/rowrgbvec

No headset, sockets, GPU, installation or production configuration is accessed.
Headset testing remains suspended. RESULTS.txt records the executed observations.
