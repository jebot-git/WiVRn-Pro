Native custom-test RRB32 adapter
==============================

Imported exact RGB row codecs and persistent workers derive from the pinned
files in import-provenance.json. Those files have include-path changes and two warning-only line breaks;
native.h is the new integration adapter. No wire format was changed. Band-view
source direct.cpp is compiled once alongside the existing exact VR444 decoder.
All code is opt-in to the dedicated WIVRN_CODEC_TESTS build.

WCT1 kind4 carries one canonical RRB1 eye with32-row blocks and zero WPC flags.
Per-eye dimensions are bounded by4096 each and4194304 pixels. Joint admission
counts BOTH48-byte WCT1 envelopes plus both bodies against262144 bytes. This is
an application payload cap, not a transport/IP/physical-wire or RSS bound.

PairDecoder requires exactly one acquire call per eye of one already-admitted
frame. It checks complete inner CRC/LZ4 grammar, identity, equal shape and the
joint cap before decoding. A generation owns one persistent2- or4-worker pool.
The shared result lease cannot be reused while held. Both native eye jobs drain
before the admission coordinator starts another frame; stop wakes an orphan.
The CPU result contains exact opaque RGBA; residual-only results contain fully
decompressed, un-restored RGB plus checked block metadata for the staged GPU
helper. Residual-only currently uses sequential CPU unpack, not worker decode.

The publication collector retains producer-completed handles privately and
returns a pair only after two successes. Any failed peer discards both. The
native stream must publish that pair under one frame-history lock and preserve
image handles until final submitted GPU readers retire, including teardown.

Source modes:
  rrb32-q1: direct exact opaque RGB from the post-foveation codec input; Q must1.
  rrb32-vr444: existing GPU VR444 encode at requestedQ followed by exact band-view
    transcode. Q8 remains lossy relative to source; only decoded VR444 RGB is exact.
Neither mode subsamples chroma. Existing VR444 mode remains an explicit control;
there is no automatic quantizer change or silent fallback on pair-cap rejection.
No full native/headset correctness or latency claim follows from component tests.
