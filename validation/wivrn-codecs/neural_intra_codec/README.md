# Small learned intra-only codec

This branch starts an executable neural codec experiment. It supplies a trainable
standard-library Python network, a bounded NIC1 bitstream, independent file
encoder/decoder CLIs, and cheap synthetic CI checks. It is a **new_lossy**
baseline: block averaging discards texture and the two-dimensional learned
bottleneck further changes color. No trained quality, speed, compression advantage,
WiVRn integration, device capability or latency-gate claim is made by the source.

The model is an MLP with dimensions 3 -> 8 -> 2 -> 8 -> 3. Training minimizes
block-mean RGB squared error, using tanh hidden layers, sigmoid bottleneck/output,
uint8 latents, and a straight-through quantization gradient. Reconstruction paints
each spatial block with one learned color. Final training receipts separately
measure actual reconstructed RGB8 MSE over every pixel in each declared split.
There is no entropy coder or inter-frame state. Rate is fixed by block size;
learning changes reconstruction, not the number of latent bytes.

Read [the common contract](../research_workflow/CONTRACT.md) and
[run_pair documentation](../research_workflow/README.md). This directory does
not change native WiVRn, original codec branches, transports, gaze behavior or
headset settings. Independent per-eye gaze is unused here.

## Run the small CPU experiment

Python 3.11+ and its standard library are sufficient. No pretrained model,
package installation, CUDA, network access or device is required.

    python3 -m unittest discover -s validation/wivrn-codecs/neural_intra_codec -p 'test_*.py' -v
    python3 validation/wivrn-codecs/neural_intra_codec/smoke.py --output-dir /tmp/neural-intra-smoke-UNIQUE

The smoke creates six tiny synthetic RGB8 files, one coherent stereo pair per
split, performs 120 SGD updates, then invokes independent encoder and decoder
processes through run_pair on one synthetic held-out pair. CI archives the model,
exact config/index/input files, bitstreams, decoder outputs and receipts.
Synthetic results are debugging evidence, not representative held-out quality.

Train on a separately frozen, honestly labeled RGB8 dataset:

    python3 validation/wivrn-codecs/neural_intra_codec/codec.py train \
      --dataset /absolute/data/dataset.json \
      --config validation/wivrn-codecs/neural_intra_codec/config.json \
      --output-dir /absolute/evidence/train-UNIQUE \
      --epochs 20 --max-blocks 256 --learning-rate 0.1 --seed 7

The bundle contains model.json, a byte-identical config.json, and
training-receipt.json. Defaults bound training to 5,120 updates; CLI hard limits
are 100 epochs and 4,096 selected blocks. Selection takes the first declared train
blocks up to the limit, then shuffles them with the recorded seed. Validation/test
pixels never enter SGD, stopping, parameter selection or normalization. Their
quality metrics are reported afterward. Fix training choices before using a final
test; changing choices after viewing it invalidates a clean held-out comparison.

The receipt binds dataset/config/model/source bytes and records the Python runtime.
Its source-file hash is **not a pinned executed Git revision or a completed common
experiment manifest**. Preserve the bundle, repin candidate and executed harness
commits in a separate common manifest, and bind all emitted streams/outputs.
The initial source contains no measured receipt or pre-trained model.

## Per-eye adapters and whole-pair admission

Use absolute script/model/config paths because run_pair runs adapters in its new
output directory. These are literal JSON argument arrays; replace /absolute/repo
and /absolute/evidence with your actual paths. Decoder dependencies are only its
bitstream, model, config, expected dimensions and eye. It receives no source image,
private tensor, prior frame, encoder state or hidden training cache.

    python3 validation/wivrn-codecs/research_workflow/run_pair.py \
      --left /absolute/data/left.rgb --right /absolute/data/right.rgb \
      --width 576 --height 640 \
      --encoder-argv '["python3","/absolute/repo/validation/wivrn-codecs/neural_intra_codec/codec.py","encode","--input","{input}","--output","{output}","--width","{width}","--height","{height}","--eye","{eye}","--model","/absolute/evidence/train-UNIQUE/model.json","--config","/absolute/evidence/train-UNIQUE/config.json"]' \
      --decoder-argv '["python3","/absolute/repo/validation/wivrn-codecs/neural_intra_codec/codec.py","decode","--input","{input}","--output","{output}","--width","{width}","--height","{height}","--eye","{eye}","--model","/absolute/evidence/train-UNIQUE/model.json","--config","/absolute/evidence/train-UNIQUE/config.json"]' \
      --output /absolute/evidence/pair-UNIQUE \
      --stereo-payload-cap 262144

Encoder --max-bytes can lower its **per-eye** 262,144-byte hard bound. That bound
does not establish stereo admission. run_pair counts both complete stream files,
including two NIC1 headers, before whole-pair admission. It excludes a production
stereo identity envelope, packetizer, FEC, crypto, retransmission and wire overhead.
For 576x640/eye, block size 4 mathematically gives 46,168 bytes/eye and 92,336
bytes/pair. Block size 2 gives 368,816 bytes/pair and is rejected under a 262,144
pair cap. These are format arithmetic, not execution or compression-quality results.

Raw input must be tightly packed row-major RGB8. Decoder output has identical
dimensions/layout, with changed values. Files must be regular, bounded and outputs
new; adapters return 0 on success and 2 on invalid input/failure. A failed or
over-cap pair has no fallback in this prototype and must remain in offered/drop
accounting. Offline run_pair rejects both eyes together. Production coherent
pair/source/warp identity, retirement, congestion and native GPU/transport
integration remain pending. Header eye checks alone do not prove that integration.

## Dataset index and leakage rejection

fixtures.py documents a complete minimal synthetic index. Schema is
neural-intra-dataset-v1 with a samples array. Every sample has exactly:

* id, split (train/validation/test), source_id, scene_id, session_id;
* pair_id, frame_id, warp_id, eye (left/right);
* format (rgb8), quantization (source-rgb8), width, height;
* timestamp_ns and clock_domain;
* confined relative .rgb path and SHA-256 of its exact packed pixel bytes.

Every pair must contain both eyes with matching source/frame/warp/dimensions,
timestamp/clock and split. All source, scene, session and identical-content groups
must be disjoint across splits. The whole index and every sample hash are checked
before model initialization or training. Assign correlated stereo/neighboring
frames the same source/scene/session groups before freezing the index. Checks
cannot infer truthful provenance from relabeled files. Historical timing receipts,
branch summaries and result tables are not an accepted sample schema or target.

Bounds: 6..256 samples, 64 MiB total raw dataset, dimensions 1..4096 with at most
1,048,576 pixels/eye, 256 KiB index, 4 KiB config, 64 KiB model. A model is data-only
JSON with exact layer shapes and finite weights bounded in magnitude by 100.
No pickle, torch checkpoint, code evaluation or dynamically imported model exists.

## NIC1 experimental bitstream

All integers are little-endian. Exactly one image and no trailing data are allowed.

| Offset | Field |
| --- | --- |
| 0 | Magic NIC1 (4 bytes) |
| 4 | Version 1 (uint8) |
| 5 | Eye: 0 left, 1 right (uint8) |
| 6 | Flags: exactly 1, meaning new_lossy (uint8) |
| 7 | Reserved: zero |
| 8 | Header size: 88 (uint16) |
| 10, 12 | Width and height (uint16 each) |
| 14, 15 | Block size and latent channels=2 (uint8 each) |
| 16 | Payload bytes (uint32) |
| 20 | SHA-256 of exact model file bytes (32 bytes) |
| 52 | SHA-256 of exact config file bytes (32 bytes) |
| 84 | CRC32 of bytes 0..83 concatenated with payload (uint32) |
| 88 | Two uint8 latent values per block, raster order |

Payload length is exactly 2*ceil(width/block_size)*ceil(height/block_size), including
partial edge blocks. Decoder validates shape, exact length, flags, eye, IDs and
checksum before pixel allocation. No decompression or inferred allocation size is
accepted. CRC detects accidental corruption and is not authentication. A party
able to rewrite content and checksum can construct another valid lossy stream.

Model configuration fixes color, quantization, quality arm and architecture.
Changing config serialization intentionally changes its byte identity and requires
a matching model binding. DenseAutoencoder.initialize/train_step/encode_latent/
decode_latent and the explicit MODELS registry are replaceable model hooks.
New dimensions/latent layout/architecture require a new versioned architecture ID,
validator/tests and, when needed, bitstream version. No model is selected by
untrusted Python import names. Cross-runtime bit-exact neural math is unqualified.

## Evidence limits and later live hooks

Tests cover learned updates, independent decoding after deleting the source,
actual byte counts, partial edge blocks, malformed/repaired-CRC headers,
truncation/trailing data, identity mismatch, caps, invalid weights, frozen sample
hashes, split leakage before training, coherent source pairs and exclusive output.
The shared manual neural_hooks.py harness may later record native adapter/device
captures. This file codec is not that native adapter; adding a plan does not create
device evidence. Any run_pair wall time includes Python startup and file I/O.
No wire, GPU, live cadence, source-age or headset metric is filled from it.
