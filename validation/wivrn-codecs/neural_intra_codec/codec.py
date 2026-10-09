#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Small learned intra-only RGB8 codec. Explicit new_lossy research baseline."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import random
import stat
import struct
import sys
import zlib

ARCH = "block-mean-mlp-v1"
SHAPES = (3, 8, 2, 8, 3)
MAX_PIXELS = 1024 * 1024
MAX_STREAM = 262144  # per-eye file bound; stereo admission belongs to run_pair
MAX_MODEL = 65536
HEADER = struct.Struct("<4sBBBBHHHBBI32s32sI")
CONFIG = {"schema": 1, "architecture": ARCH, "block_size": 4,
          "latent_channels": 2, "quantization": "uint8",
          "color": "rgb8", "quality_arm": "new_lossy"}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode()


def read_file(path, limit, exact=None):
    path = Path(path)
    info = path.lstat()
    need(stat.S_ISREG(info.st_mode), "input must be a regular non-symlink file")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        need((info.st_dev, info.st_ino) == (opened.st_dev, opened.st_ino)
             and stat.S_ISREG(opened.st_mode), "input changed while opening")
        need(opened.st_size <= limit, "input exceeds byte bound")
        raw = stream.read(limit + 1)
    need(len(raw) <= limit and (exact is None or len(raw) == exact),
         "invalid input byte count")
    return raw


def write_new(path, data):
    with Path(path).open("xb") as stream:
        stream.write(data)


def unique_object(items):
    result = {}
    for key, value in items:
        need(key not in result, "duplicate JSON key")
        result[key] = value
    return result


def parse_json(raw):
    return json.loads(raw, object_pairs_hook=unique_object,
                      parse_constant=lambda value: (_ for _ in ()).throw(
                          ValueError("nonfinite JSON number")))


def integer(value, low, high, name):
    need(type(value) is int and low <= value <= high, "invalid " + name)
    return value


def dimensions(width, height):
    integer(width, 1, 4096, "width")
    integer(height, 1, 4096, "height")
    need(width * height <= MAX_PIXELS, "pixel bound exceeded")


def load_config(path):
    raw = read_file(path, 4096)
    config = parse_json(raw)
    need(type(config) is dict and set(config) == set(CONFIG)
         and type(config["schema"]) is int, "config schema")
    need(all(config[k] == v for k, v in CONFIG.items() if k != "block_size"),
         "unsupported configuration")
    need(type(config["block_size"]) is int and config["block_size"] in (2, 4, 8, 16),
         "unsupported block size")
    return config, digest(raw), raw


def sigmoid(value):
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, value))))


class DenseAutoencoder:
    """Replaceable model hook; changed architecture requires a new architecture ID."""
    def __init__(self, state):
        need(type(state) is list and len(state) == 4, "model layer count")
        for layer, inputs, outputs in zip(state, SHAPES, SHAPES[1:]):
            need(type(layer) is dict and set(layer) == {"weight", "bias"}, "layer schema")
            need(type(layer["weight"]) is list and len(layer["weight"]) == outputs
                 and type(layer["bias"]) is list and len(layer["bias"]) == outputs,
                 "model shape")
            for row in layer["weight"]:
                need(type(row) is list and len(row) == inputs, "weight shape")
            for value in layer["bias"] + [v for row in layer["weight"] for v in row]:
                need(type(value) in (int, float) and math.isfinite(value)
                     and abs(value) <= 100, "nonfinite or unbounded model weight")
        self.state = state

    @classmethod
    def initialize(cls, seed):
        rng = random.Random(seed)
        return cls([{"weight": [[rng.uniform(-0.5, 0.5) for _ in range(a)]
                               for _ in range(b)], "bias": [0.0] * b}
                    for a, b in zip(SHAPES, SHAPES[1:])])

    def layer(self, index, values):
        layer = self.state[index]
        activation = sigmoid if index in (1, 3) else math.tanh
        return [activation(bias + sum(w * x for w, x in zip(row, values)))
                for row, bias in zip(layer["weight"], layer["bias"])]

    def encode_latent(self, rgb):
        return bytes(int(v * 255 + 0.5) for v in self.layer(1, self.layer(0, rgb)))

    def decode_latent(self, latent):
        values = self.layer(3, self.layer(2, [v / 255.0 for v in latent]))
        return bytes(int(v * 255 + 0.5) for v in values)

    def train_step(self, target, learning_rate):
        values = [target]
        activations = []
        for index in range(4):
            output = self.layer(index, values[-1])
            activations.append(output)
            # Straight-through estimator for the actual uint8 bottleneck.
            values.append([int(x * 255 + 0.5) / 255.0 for x in output]
                          if index == 1 else output)
        gradient = [2.0 * (a - b) / 3.0 for a, b in zip(values[-1], target)]
        for index in reversed(range(4)):
            output = activations[index]
            delta = [g * (y * (1 - y) if index in (1, 3) else 1 - y * y)
                     for g, y in zip(gradient, output)]
            layer = self.state[index]
            gradient = [sum(delta[j] * layer["weight"][j][i] for j in range(len(delta)))
                        for i in range(len(values[index]))]
            for j, d in enumerate(delta):
                layer["bias"][j] -= learning_rate * d
                for i, x in enumerate(values[index]):
                    layer["weight"][j][i] -= learning_rate * d * x


# Versioned registry is the architecture extension point; no dynamic code loading.
MODELS = {ARCH: DenseAutoencoder}


def load_model(path, config_hash):
    raw = read_file(path, MAX_MODEL)
    model = parse_json(raw)
    need(type(model) is dict and set(model) == {"schema", "architecture", "quality_arm",
                                               "config_sha256", "state", "training"},
         "model schema")
    need(type(model["schema"]) is int and model["schema"] == 1
         and model["architecture"] == ARCH and model["quality_arm"] == "new_lossy",
         "unsupported model")
    need(model["config_sha256"] == config_hash, "model/config mismatch")
    need(type(model["training"]) is dict, "model training metadata")
    return MODELS[model["architecture"]](model["state"]), digest(raw)


def blocks(raw, width, height, size):
    for y in range(0, height, size):
        for x in range(0, width, size):
            sums = [0, 0, 0]
            count = min(size, height - y) * min(size, width - x)
            for yy in range(y, min(height, y + size)):
                for xx in range(x, min(width, x + size)):
                    offset = (yy * width + xx) * 3
                    for channel in range(3):
                        sums[channel] += raw[offset + channel]
            yield [s / (count * 255.0) for s in sums]


def payload_size(width, height, size):
    return ((width + size - 1) // size) * ((height + size - 1) // size) * 2


def encode(raw, width, height, eye, model, model_hash, config, config_hash, cap=MAX_STREAM):
    dimensions(width, height)
    integer(cap, HEADER.size + 2, MAX_STREAM, "encoded byte cap")
    need(eye in ("left", "right"), "eye")
    need(len(raw) == width * height * 3, "RGB8 byte count")
    size = config["block_size"]
    count = payload_size(width, height, size)
    need(HEADER.size + count <= cap, "encoded stream exceeds cap")
    payload = b"".join(model.encode_latent(rgb) for rgb in blocks(raw, width, height, size))
    header = HEADER.pack(b"NIC1", 1, int(eye == "right"), 1, 0, HEADER.size,
                         width, height, size, 2, count, bytes.fromhex(model_hash),
                         bytes.fromhex(config_hash), 0)
    return header[:-4] + struct.pack("<I", zlib.crc32(header[:-4] + payload)) + payload


def decode(stream, model, model_hash, config, config_hash, width, height, eye):
    need(HEADER.size <= len(stream) <= MAX_STREAM, "stream size bound")
    (magic, version, eye_id, flags, reserved, header_size, w, h, size, channels,
     count, model_id, config_id, checksum) = HEADER.unpack_from(stream)
    dimensions(w, h)
    need((magic, version, flags, reserved, header_size) == (b"NIC1", 1, 1, 0, HEADER.size),
         "unsupported header")
    need(eye in ("left", "right") and eye_id == int(eye == "right"), "eye mismatch")
    need((w, h) == (width, height), "dimension mismatch")
    need(size == config["block_size"] and channels == 2, "latent layout mismatch")
    need(model_id.hex() == model_hash and config_id.hex() == config_hash,
         "stream model/config mismatch")
    need(count == payload_size(w, h, size) and len(stream) == HEADER.size + count,
         "noncanonical payload length or trailing bytes")
    payload = stream[HEADER.size:]
    need(zlib.crc32(stream[:HEADER.size - 4] + payload) == checksum, "stream checksum")
    output = bytearray(w * h * 3)  # all bounds/identities checked before allocation
    offset = 0
    for y in range(0, h, size):
        for x in range(0, w, size):
            pixel = model.decode_latent(payload[offset:offset + 2])
            offset += 2
            row = pixel * min(size, w - x)
            for yy in range(y, min(h, y + size)):
                begin = (yy * w + x) * 3
                output[begin:begin + len(row)] = row
    return bytes(output)


SAMPLE_KEYS = {"id", "split", "source_id", "scene_id", "session_id", "pair_id",
               "frame_id", "warp_id", "eye", "format", "quantization", "timestamp_ns",
               "clock_domain", "path", "width", "height", "sha256"}


def load_dataset(path):
    """Validate the entire frozen index, split groups and bytes before any training."""
    path = Path(path)
    raw = read_file(path, 262144)
    index = parse_json(raw)
    need(type(index) is dict and set(index) == {"schema", "samples"}
         and index["schema"] == "neural-intra-dataset-v1", "dataset schema")
    samples = index["samples"]
    need(type(samples) is list and 6 <= len(samples) <= 256, "dataset sample bound")
    groups = {key: {} for key in ("source_id", "scene_id", "session_id", "sha256")}
    ids, splits, pairs = set(), set(), {}
    total = 0
    for sample in samples:
        need(type(sample) is dict and set(sample) == SAMPLE_KEYS, "sample schema")
        for key in SAMPLE_KEYS - {"width", "height", "frame_id", "warp_id", "timestamp_ns"}:
            need(type(sample[key]) is str and 0 < len(sample[key]) <= 512,
                 "invalid sample " + key)
        need(sample["id"] not in ids, "duplicate sample id")
        ids.add(sample["id"])
        split = sample["split"]
        need(split in ("train", "validation", "test"), "invalid split")
        splits.add(split)
        for key in groups:
            value = sample[key]
            need(groups[key].get(value, split) == split, key + " split leakage")
            groups[key][value] = split
        need(len(sample["sha256"]) == 64 and all(c in "0123456789abcdef" for c in sample["sha256"]),
             "invalid sample digest")
        dimensions(sample["width"], sample["height"])
        for key in ("frame_id", "warp_id", "timestamp_ns"):
            integer(sample[key], 0, 2**63 - 1, key)
        need(sample["format"] == "rgb8" and sample["quantization"] == "source-rgb8"
             and sample["eye"] in ("left", "right"), "sample format/eye")
        relative = Path(sample["path"])
        need(not relative.is_absolute() and ".." not in relative.parts
             and relative.suffix == ".rgb", "dataset RGB path must be confined and relative")
        full = path.parent / relative
        need(full.resolve().is_relative_to(path.parent.resolve()), "dataset path escape")
        total += sample["width"] * sample["height"] * 3
        need(total <= 64 * 1024 * 1024, "dataset byte bound")
        pair_key = (sample["session_id"], sample["pair_id"])
        pair = pairs.setdefault(pair_key, [])
        pair.append(sample)
    need(splits == {"train", "validation", "test"}, "all three splits required")
    for pair in pairs.values():
        need(len(pair) == 2 and {s["eye"] for s in pair} == {"left", "right"},
             "dataset requires complete stereo pairs")
        for key in ("split", "source_id", "scene_id", "frame_id", "warp_id", "width", "height",
                    "timestamp_ns", "clock_domain"):
            need(pair[0][key] == pair[1][key], "incoherent stereo " + key)
    loaded = []
    for sample in samples:
        pixels = read_file(path.parent / sample["path"], 3 * MAX_PIXELS,
                           sample["width"] * sample["height"] * 3)
        need(digest(pixels) == sample["sha256"], "dataset bytes/hash mismatch")
        loaded.append((sample, pixels))
    return loaded, digest(raw)


def train(args):
    config, config_hash, config_raw = load_config(args.config)
    samples, dataset_hash = load_dataset(args.dataset)  # fail leakage before model/init/update
    integer(args.epochs, 1, 100, "epochs")
    integer(args.max_blocks, 1, 4096, "training block budget")
    integer(args.seed, 0, 2**32 - 1, "seed")
    need(math.isfinite(args.learning_rate) and 0 < args.learning_rate <= 1, "learning rate")
    features = []
    for sample, pixels in samples:
        if sample["split"] == "train":
            for feature in blocks(pixels, sample["width"], sample["height"], config["block_size"]):
                if len(features) < args.max_blocks:
                    features.append(feature)
                else:
                    break
    need(features, "empty training population")
    model = MODELS[ARCH].initialize(args.seed)
    initial = digest(canonical(model.state))
    rng = random.Random(args.seed)
    for _ in range(args.epochs):
        rng.shuffle(features)
        for feature in features:
            model.train_step(feature, args.learning_rate)
    MODELS[ARCH](model.state)  # finite/shape validation after optimization
    training = {"dataset_sha256": dataset_hash, "seed": args.seed, "epochs": args.epochs,
                "learning_rate": args.learning_rate, "blocks": len(features),
                "block_selection": "first declared train blocks up to max_blocks; then seeded shuffle",
                "steps": args.epochs * len(features), "initial_state_sha256": initial,
                "source_scene_session_splits_checked": True}
    document = {"schema": 1, "architecture": ARCH, "quality_arm": "new_lossy",
                "config_sha256": config_hash, "state": model.state, "training": training}
    model_raw = canonical(document)
    need(len(model_raw) <= MAX_MODEL, "model byte bound")
    metrics = {}
    for split in ("train", "validation", "test"):
        squared = components = 0
        for sample, original in samples:
            if sample["split"] != split:
                continue
            stream = encode(original, sample["width"], sample["height"], sample["eye"],
                            model, digest(model_raw), config, config_hash)
            reconstructed = decode(stream, model, digest(model_raw), config, config_hash,
                                   sample["width"], sample["height"], sample["eye"])
            squared += sum((a - b) ** 2 for a, b in zip(original, reconstructed))
            components += len(original)
        metrics[split] = {"rgb8_mse": squared / components, "components": components}
    receipt = {"schema": "neural-intra-training-v1", "quality_arm": "new_lossy",
               "status": "completed", "model_sha256": digest(model_raw),
               "config_sha256": config_hash, "dataset_sha256": dataset_hash,
               "codec_source_sha256": digest(read_file(__file__, 1048576)),
               "python": sys.version, "architecture": ARCH, "training": training,
               "metrics": metrics, "wire_bytes": None, "headset_latency_ms": None,
               "limitations": ["Small block-mean bottleneck, no perceptual-quality qualification.",
                               "Offline Python execution only; no network/GPU/headset measurement.",
                               "Split integrity depends on truthful frozen source/scene/session labels."]}
    destination = Path(args.output_dir)
    destination.mkdir()  # exclusive bundle, no overwrite
    write_new(destination / "config.json", config_raw)
    write_new(destination / "model.json", model_raw)
    write_new(destination / "training-receipt.json", canonical(receipt))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    trainer = commands.add_parser("train")
    trainer.add_argument("--dataset", required=True)
    trainer.add_argument("--config", required=True)
    trainer.add_argument("--output-dir", required=True)
    trainer.add_argument("--epochs", type=int, default=20)
    trainer.add_argument("--max-blocks", type=int, default=256)
    trainer.add_argument("--learning-rate", type=float, default=0.1)
    trainer.add_argument("--seed", type=int, default=7)
    for name in ("encode", "decode"):
        adapter = commands.add_parser(name)
        for field in ("input", "output", "model", "config"):
            adapter.add_argument("--" + field, required=True)
        for field in ("width", "height"):
            adapter.add_argument("--" + field, type=int, required=True)
        adapter.add_argument("--eye", choices=("left", "right"), required=True)
        if name == "encode":
            adapter.add_argument("--max-bytes", type=int, default=MAX_STREAM)
    args = parser.parse_args(argv)
    try:
        if args.command == "train":
            train(args)
        else:
            dimensions(args.width, args.height)
            config, config_hash, _ = load_config(args.config)
            model, model_hash = load_model(args.model, config_hash)
            if args.command == "encode":
                need(HEADER.size + payload_size(args.width, args.height, config["block_size"])
                     <= args.max_bytes <= MAX_STREAM, "encoded stream exceeds cap")
                raw = read_file(args.input, 3 * MAX_PIXELS, args.width * args.height * 3)
                result = encode(raw, args.width, args.height, args.eye, model, model_hash,
                                config, config_hash, args.max_bytes)
            else:
                raw = read_file(args.input, MAX_STREAM)
                result = decode(raw, model, model_hash, config, config_hash,
                                args.width, args.height, args.eye)
            write_new(args.output, result)
        return 0
    except (ValueError, OSError, KeyError, TypeError, OverflowError, RecursionError) as error:
        print("neural-intra-codec: " + str(error), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
