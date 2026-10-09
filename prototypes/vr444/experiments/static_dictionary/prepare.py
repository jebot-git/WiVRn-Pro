#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Explicit split: two named training eyes are excluded from the held-out corpus."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("corpus", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--train", nargs=2, default=["paired-a/1080-0.payload", "paired-a/1080-1.payload"])
    args = p.parse_args()
    corpus = args.corpus.resolve()
    files = sorted(corpus.rglob("*.payload"))
    if not 2 < len(files) <= 512:
        raise ValueError("corpus file bound")
    training = {(corpus / name).resolve() for name in args.train}
    if len(training) != 2 or any(p not in files for p in training):
        raise ValueError("training paths must be distinct existing corpus files")
    if args.output.exists():
        raise ValueError("output must be new to avoid stale training/test files")
    args.output.mkdir(parents=True)
    records, raw_training, total = [], b"", 0
    for path in files:
        size = path.stat().st_size
        if not 0 < size <= 16 * 1024 * 1024:
            raise ValueError("file bound")
        total += size
        if total > 64 * 1024 * 1024:
            raise ValueError("corpus bound")
        data = path.read_bytes()
        train = path in training
        name = str(path.relative_to(corpus))
        records.append(dict(path=name, training=train, bytes=size, sha256=hashlib.sha256(data).hexdigest()))
        if train:
            raw_training += data
        else:
            out = args.output / "heldout" / name
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, out)
    dictionary = raw_training[-65536:]
    if len(dictionary) < 8:
        raise ValueError("dictionary bound")
    (args.output / "dictionary.bin").write_bytes(dictionary)
    receipt = dict(schema=1, method="last64KiB of two explicitly named training eyes, no learned optimization",
                   corpus_origin=str(corpus), source_files=records, training_files=len(training),
                   heldout_files=len(files) - len(training), dictionary_bytes=len(dictionary),
                   dictionary_sha256=hashlib.sha256(dictionary).hexdigest(),
                   limitation="nearby held-out captures share a small static scene; no game-domain generalization")
    (args.output / "split.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({k: receipt[k] for k in ("training_files", "heldout_files", "dictionary_bytes", "dictionary_sha256")}))


if __name__ == "__main__":
    main()
