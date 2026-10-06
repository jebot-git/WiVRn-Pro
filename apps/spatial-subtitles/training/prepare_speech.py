#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Download pinned, attributed FLEURS splits, then extract a bounded pilot set."""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
from huggingface_hub import hf_hub_download
import pyarrow.parquet as pq
import soundfile as sf

REVISION = "168de341b3db6859a9bac1c50a2ef5e3b47647e0"
LANGUAGES = {"ko_kr": "ko", "cmn_hans_cn": "zh", "ja_jp": "ja", "th_th": "th"}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--train-per-language", type=int, default=60)
    p.add_argument("--eval-per-language", type=int, default=12)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    def extract(task):
        config, split, count = task
        path = hf_hub_download(
            "google/fleurs",
            f"{config}/{split}/0000.parquet",
            repo_type="dataset",
            revision=REVISION,
        )
        records = []
        for batch in pq.ParquetFile(path).iter_batches(batch_size=16):
            for row in batch.to_pylist():
                data, sr = sf.read(io.BytesIO(row["audio"]["bytes"]), dtype="float32")
                if sr != 16000 or data.ndim != 1 or not 1 <= len(data) / sr <= 25:
                    continue
                target = (
                    args.output
                    / config
                    / split
                    / (str(row["id"]) + "-" + str(len(records)) + ".wav")
                )
                target.parent.mkdir(parents=True, exist_ok=True)
                sf.write(target, data, sr, subtype="PCM_16")
                records.append(
                    dict(
                        path=str(target.resolve()),
                        language=LANGUAGES[config],
                        split=split,
                        id=row["id"],
                        text=row["raw_transcription"],
                        seconds=len(data) / sr,
                        sha256=hashlib.sha256(target.read_bytes()).hexdigest(),
                    )
                )
                if len(records) >= count:
                    break
            if len(records) >= count:
                break
        if len(records) != count:
            raise RuntimeError("Insufficient eligible samples")
        print(config, split, len(records), flush=True)
        return records

    tasks = [
        (c, s, n)
        for c in LANGUAGES
        for s, n in [
            ("train", args.train_per_language),
            ("validation", args.eval_per_language),
        ]
    ]
    with ThreadPoolExecutor(max_workers=3) as pool:
        rows = sum(pool.map(extract, tasks), [])
    manifest = dict(
        dataset="google/fleurs",
        revision=REVISION,
        licence="CC-BY-4.0",
        attribution="Google FLEURS; Conneau et al., FLEURS: Few-shot Learning Evaluation of Universal Representations of Speech (2022)",
        source="https://huggingface.co/datasets/google/fleurs",
        rows=rows,
    )
    (args.output / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2)
    )
    print(
        "Prepared",
        len(rows),
        "samples; official validation split is never trained on",
        flush=True,
    )


if __name__ == "__main__":
    main()
