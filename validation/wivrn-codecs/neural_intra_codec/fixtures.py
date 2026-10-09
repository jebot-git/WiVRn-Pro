#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Tiny explicitly synthetic disjoint stereo fixtures; never historical receipts."""
import json
from pathlib import Path
import codec


def create(root):
    root = Path(root)
    root.mkdir()
    samples = []
    for number, split in enumerate(("train", "validation", "test")):
        for eye_number, eye in enumerate(("left", "right")):
            width, height = 17, 9
            pixels = bytes((20 + number * 19 + eye_number * 7 + x * 4 + y * 3 + c * 31) % 256
                           for y in range(height) for x in range(width) for c in range(3))
            name = split + "-" + eye + ".rgb"
            codec.write_new(root / name, pixels)
            samples.append({"id": split + "-" + eye, "split": split,
                            "source_id": "synthetic-source-" + split,
                            "scene_id": "synthetic-scene-" + split,
                            "session_id": "synthetic-session-" + split,
                            "pair_id": "synthetic-pair-" + split, "frame_id": number + 1,
                            "warp_id": number + 10, "eye": eye, "format": "rgb8",
                            "quantization": "source-rgb8", "timestamp_ns": number * 1000000000,
                            "clock_domain": "synthetic-logical", "path": name,
                            "width": width, "height": height, "sha256": codec.digest(pixels)})
    codec.write_new(root / "dataset.json",
                    codec.canonical({"schema": "neural-intra-dataset-v1", "samples": samples}))
    codec.write_new(root / "config.json", codec.canonical(codec.CONFIG))
    return root / "dataset.json"
