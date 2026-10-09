#!/usr/bin/env python3
"""Tiny synthetic artifact fixture and bounded training; never research or hardware evidence."""
import argparse
from pathlib import Path

import core


def fixture(directory):
    directory = Path(directory)
    directory.mkdir()
    records = []
    values = {"train": [0, 16, 32, 112, 128, 144, 208, 224, 240],
              "validation": [8, 120, 216], "test": [24, 136, 232]}
    def save(name, data):
        (directory / name).write_bytes(data)
        return {"path": name, "sha256": core.sha256(data)}
    for split, levels in values.items():
        for number, level in enumerate(levels):
            pair = split + "-" + str(number)
            for eye, offset in (("left", 0), ("right", 1)):
                name = pair + "-" + eye
                rgb = bytes([level + offset]) * 24
                source = save(name + ".rgb", rgb)
                best = min(2, level // 85)
                outcomes = {}
                for i, mode in enumerate(core.MODES):
                    # Arbitrary fixture bytes, not encoded RGB or RRF/RRB bitstreams.
                    encoded = save(name + "." + mode, bytes([i]) * (12 if i == best else 40 + i))
                    outcomes[mode] = {"encoded": encoded, "decoded": source}
                records.append(dict(id=name, scene=split + "-scene", session=split + "-session",
                                    split=split, stereo_pair_id=pair, source_frame_id=pair,
                                    warp_id=pair, eye=eye, block_index=0, width=4, rows=2,
                                    source=source, outcomes=outcomes))
    data = {"schema_version": 1, "profile": core.PROFILE, "quality_contract": "lossless",
            "evidence": "synthetic_smoke", "provenance": {
                "candidate_commit": "1" * 40, "baseline_commit": "2" * 40,
                "harness_commit": "3" * 40, "artifacts": []}, "records": records}
    path = directory / "index.json"
    core.write_json(path, data)
    return path, core.sha256(path.read_bytes())


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="new fixture/output directory")
    args = parser.parse_args()
    path, digest = fixture(args.output)
    data, records = core.load_dataset(path, digest)
    model = core.train(data, records, digest, epochs=60)
    core.write_json(path.parent / "model.json", model)
    core.write_json(path.parent / "evaluation.json", core.evaluate(model, data, records, digest))
    core.write_json(path.parent / "fixture-receipt.json",
                    {"evidence": "synthetic_smoke", "dataset_sha256": digest,
                     "model_sha256": core.sha256(core.canonical(model)),
                     "training_records": 18, "test_records": 6,
                     "research_training": False, "hardware_execution": False})


if __name__ == "__main__":
    main()
