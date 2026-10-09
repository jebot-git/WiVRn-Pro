WiVRn CODEC TEST frame compression, development 0.11

frame_compression.h exposes an explicitly configured, stateless WPC1 admission
layer. Link vendor/lz4.c as C. All wrappers require C++20. Header-only functions
are namespace wivrn::codec_test::compression; no dependency beyond libc/libstdc++
and this vendor LZ4 source. Android/Linux aarch64 builds dispatch CRC through
HWCAP_CRC32 to target("crc") IEEE hardware CRC; other builds use the table path.

Selection select(span<const uint8_t> raw, Workspace&, size_t minimum_saving_bytes=64)
returns payload, attempted, compressed, original_bytes, wire_bytes. The returned
payload is either a complete method-1 WPC1 frame or the original input span. It
never increases selected payload size, even with minimum_saving_bytes=0. Inputs
of 16 MiB-23 through 16 MiB bytes bypass compression (the legacy WPC1 raw escape
must fit its 24-byte header). Inputs must be nonempty and at most 16 MiB. Tiny
inputs and impossible saving thresholds bypass the attempt. Raw fallback has
compressed=false and NO WPC1 header. Select only updates compression metadata;
it supplies no frame identity, authentication, negotiation or transport change.

span<const uint8_t> decode(span<const uint8_t> wrapped, Workspace&) validates
method-1 compressed WPC1 (strictly shorter than its claimed decoded size), all
framing before output allocation, LZ4 exact output size, then IEEE CRC32 before
returning. Any failure throws std::runtime_error and clears decoded size. A
receiver MUST NOT invoke its codec decoder after an exception. Method-0 WPC1 raw
escapes are rejected: callers send raw with no compression flag instead. WPC1
input_limit is 16 MiB minus 24; compressed wire limit is 16 MiB. CRC detects
accidental corruption; it is not authentication against an active attacker.

Use separate encoder and decoder Workspace instances per simultaneous operation.
Views borrow storage and expire on the next workspace mutation/destruction; raw
selection views borrow caller input. Every input overlapping any allocated
workspace buffer is rejected, even when current vector size has shrunk. Returned
views may be copied to independent storage for later reuse. The helper retains
vector capacity after warmup but resets frame/block state for each call. There
are no cross-frame dictionaries or dependencies. Scratch capacity is at most
LZ4_compressBound(16 MiB-24), slightly above 16 MiB; input/output bounds remain
16 MiB. Allocation failure propagates without admitting a result.

WPC1 source derives from prototypes/vr444/experiments/native_lz4/wpc.hpp (MIT),
with namespace and automatic runtime ARM CRC guard adjustments. Header/block
encoding is unchanged; whole-frame profile uses block size 16 MiB. The pinned
vendor directory preserves LZ4 v1.10.0, BSD-2-Clause license and source hashes.
