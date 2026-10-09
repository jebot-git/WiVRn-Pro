Immutable independent-frame dictionary experiment (DPC1)
=======================================================

Status: host-only offline research. This branch does not alter production wire
formats, launch a listener, deploy to Android, or access a headset. It asks whether
an already provisioned 64 KiB dictionary saves enough bytes to justify its server
and client work while preserving frame independence. No sub-15 ms result is claimed.

Wire and lifetime contract
--------------------------
DPC1 is a separate experimental wrapper, not a WPC1 extension or a negotiated
production codec. All integer fields are little endian. Header size is 64 bytes:

  0..3 magic DPC1; 4 version 1; 5 method 1; 6..7 reserved zero
  8 raw length u32; 12 compressed length u32; 16 dictionary length u32
  20 original raw CRC32; 24..55 dictionary SHA-256
  56 reserved u32 zero; 60 CRC32 of preceding 60 header bytes
  64.. exact LZ4 block bytes; no trailing bytes

Input and wire are capped at 16 MiB, dictionary at 8..65,536 bytes. The decoder
requires compressed wire to be smaller than raw, validates header, lengths and
dictionary identity before allocating output, bounds the LZ4 output, requires
exact decompressed length, then verifies raw CRC. It clears decoded output on
malformed input. Input/workspace aliasing is an API precondition rejected before
mutation. Workspace allocations and capacities remain bounded and reusable.

Dictionary owns copied immutable storage, SHA-256 and a prepared LZ4 context;
copy/move are disabled so borrowed pointers stay valid. Reset and attach occur
before EACH frame. No prior frame is part of decoder history: old frames remain
independently decodable out of order. Encoder and decoder must keep the same
dictionary alive. SHA-256 names bytes; CRC detects corruption. Neither authenticates
peers. Dictionary provisioning, trust, negotiation and replacement are NOT
implemented. Missing/wrong dictionaries reject, never fetch on the critical path.

An encoder may decline DPC1 and return the borrowed original input. The benchmark
first computes the existing plain LZ4/WPC1-or-raw choice, then attempts DPC1 and
selects it only if strictly smaller. Timed server work includes BOTH attempts,
copies, framing and CRC. The 64-byte DPC1 header counts toward selection; minimum
gain over raw is 64 bytes. Raw high-entropy fallback remains exact. One-time
dictionary preparation/provisioning is separate. Per-eye exact bytes and existing
VR444 decoded pixels are checked; this preserves Q8's already decoded content and
does not make a lossy source lossless. Stereo/epoch/deadline publication remains an
outer transport obligation; this isolated wrapper has no publication path.

Build and reproduce
-------------------
Requires existing clang/clang++ and OpenSSL development headers/library. Vendored
LZ4 v1.10.0 is reused. No package installation is performed.

  prototypes/vr444/experiments/static_dictionary/build.sh
  /tmp/wivrn-static-dictionary-build/test
  python3 prototypes/vr444/experiments/static_dictionary/prepare.py \
    validation/results/codec-viability-0.8.0/compact/corpus \
    /tmp/sub15-static-dictionary-corpus
  /tmp/wivrn-static-dictionary-build/bench \
    /tmp/sub15-static-dictionary-corpus/heldout \
    /tmp/sub15-static-dictionary-corpus/dictionary.bin 31 > /tmp/dictionary-bench.json
  python3 prototypes/vr444/experiments/static_dictionary/summarize.py \
    /tmp/dictionary-bench.json /tmp/sub15-static-dictionary-corpus/split.json

prepare.py requires a new output directory. It selects two explicitly named
training eye captures (paired-a/1080-0 and 1080-1), takes the final 64 KiB of their
concatenation, and excludes BOTH files from 36 held-out cases. No learned search
or optimization sees the held-out files. Source hashes and split are preserved;
actual captured content and dictionary bytes are local-only. These nearby small
static-scene captures are not an independent game-domain evaluation. Both eyes,
Q1, Q8 and high-entropy synthetics remain in the test set.

Coverage and measurement interpretation
--------------------------------------
ASan+UBSan: 65 roundtrips, 9,895 rejected inputs/preconditions, 10,000 mutations,
all truncations of one independent block, repaired-header malformed fields,
wrong dictionaries, maximum 16 MiB raw input, boundary fallback, aliasing and
out-of-order decode. A known SHA-256 vector is cross-checked. Acceptable alternative
compressed spellings must decode to identical original bytes.

Benchmark: 3 warmups + 31 repetitions per profile; rotate profile order each
iteration; deterministic shuffled case order. Reused separate transmit/receive
workspaces. Timed client work includes unwrap, direct VR444 preflight and exact
reconstruction. Reference pixel comparison is outside timing. Both dictionary
attempts are decoded and checked outside timing even if adaptive selection rejects
them. Report per-case samples, wire bytes, selected mode, candidate sizes and exact
checks. Group timing summaries are means of case p50s, NOT pooled p95.

The first fast-context initialization can include lazy OpenSSL initialization;
the single fast/slow setup observations are not a controlled comparison. The
run uses one desktop i7-12700 host without affinity/frequency/thermal control.
Collaborator benchmark/build windows are coordinated, but normal desktop and
existing services continue. No network, mobile decode, headset compositor, optics,
or receiver-to-submit timing is measured. Wire reduction upstream of first receive
would not automatically reduce the strict receive-to-submit metric.

Results and decision
--------------------
See results/summary.json and results/measurement-provenance.json. Dictionary
adaptation saved zero bytes across all 22 held-out captured eyes. Only synthetic
gradient-q8 selected DPC1: 9,704 -> 9,585 bytes (119 saved). That does not repay the
65,536-byte dictionary across one traversal of this corpus. The adaptive path adds
an extra compression attempt to every eye. Do not promote this profile based on
these data. Keep the independent dictionary contract as a bounded comparison for
future broader corpora; do not infer cross-game behavior or change quality.

Primary API references
----------------------
LZ4 v1.10.0 loadDict, loadDictSlow and attach_dictionary contract, including
dictionary lifetime and attachment applying to the next compression call:
https://github.com/lz4/lz4/blob/v1.10.0/lib/lz4.h
OpenSSL SHA256 one-shot digest API used once at dictionary creation:
https://docs.openssl.org/3.0/man3/SHA256_Init/
