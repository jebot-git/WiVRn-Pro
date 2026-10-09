#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Cheap synthetic CPU training plus independent per-eye codec processes for CI."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import codec
import fixtures

HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_dir.absolute()
    root.mkdir()
    dataset = fixtures.create(root / "data")
    bundle = root / "trained"
    subprocess.run([sys.executable, str(HERE / "codec.py"), "train",
                    "--dataset", str(dataset), "--config", str(dataset.parent / "config.json"),
                    "--output-dir", str(bundle), "--epochs", "4", "--max-blocks", "32",
                    "--seed", "7"], check=True, timeout=30)
    commands = {}
    for operation in ("encode", "decode"):
        commands[operation] = [sys.executable, str(HERE / "codec.py"), operation,
                               "--input", "{input}", "--output", "{output}",
                               "--width", "{width}", "--height", "{height}", "--eye", "{eye}",
                               "--config", str(bundle / "config.json"),
                               "--model", str(bundle / "model.json")]
    subprocess.run([sys.executable, str(HERE.parent / "research_workflow" / "run_pair.py"),
                    "--left", str(dataset.parent / "test-left.rgb"),
                    "--right", str(dataset.parent / "test-right.rgb"),
                    "--width", "17", "--height", "9",
                    "--encoder-argv", json.dumps(commands["encode"]),
                    "--decoder-argv", json.dumps(commands["decode"]),
                    "--output", str(root / "pair"), "--stereo-payload-cap", "262144"],
                   check=True, timeout=60)
    receipt = {"schema": "neural-intra-smoke-v1", "population": "one synthetic held-out stereo pair",
               "quality_arm": "new_lossy", "runtime": "Python standard library, CPU",
               "measurements": "actual offline files and RGB8 error; subprocess timings diagnostic only",
               "training_steps": 120, "headset_operations": False, "network_operations": False,
               "files_sha256": {str(p.relative_to(root)): codec.digest(p.read_bytes())
                                for p in sorted(root.rglob("*")) if p.is_file()}}
    codec.write_new(root / "smoke-receipt.json", codec.canonical(receipt))


if __name__ == "__main__":
    main()
