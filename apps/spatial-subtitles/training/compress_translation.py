#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit NNCF INT8 weight compression; never label an FP32 export as INT8."""

import argparse, json, shutil
from pathlib import Path
import openvino as ov, nncf

p = argparse.ArgumentParser()
p.add_argument("source", type=Path)
p.add_argument("target", type=Path)
a = p.parse_args()
a.target.mkdir(exist_ok=False)
for path in a.source.iterdir():
    if path.suffix not in (".xml", ".bin") and path.is_file():
        shutil.copy2(path, a.target / path.name)
report = {}
for path in a.source.glob("*.xml"):
    if "encoder" in path.name or "decoder" in path.name:
        m = ov.Core().read_model(path)
        m = nncf.compress_weights(m, mode=nncf.CompressWeightsMode.INT8_ASYM)
        ov.save_model(m, a.target / path.name)
    else:
        # Tokenizer graphs contain extension operations and are not neural weights.
        shutil.copy2(path, a.target / path.name)
        shutil.copy2(path.with_suffix(".bin"), a.target / path.with_suffix(".bin").name)
    report[path.name] = dict(
        before_bytes=path.with_suffix(".bin").stat().st_size,
        after_bytes=(a.target / path.with_suffix(".bin").name).stat().st_size,
    )
(a.target / "compression.json").write_text(json.dumps(report, indent=2))
print(report)
