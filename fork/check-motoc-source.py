#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verify the Motoc source pin and recorded integration changes offline."""
import hashlib
import json
from pathlib import Path
root = Path(__file__).resolve().parents[1]
meta = json.loads((root / 'fork/motoc-import.json').read_text())
for name in set(meta['sha256']) | set(meta['fork_sha256']):
    expected = meta['fork_sha256'].get(name, meta['sha256'].get(name))
    actual = hashlib.sha256((root / 'server/motoc/upstream' / name).read_bytes()).hexdigest()
    assert actual == expected, f'Unrecorded Motoc change: {name}'
print(f"Motoc source pin {meta['head']}: verified")
