#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Audit the pinned WayVR import and the small integration patch; no network or XR."""
import hashlib
import json
from pathlib import Path
import subprocess
root = Path(__file__).resolve().parents[1]
meta = json.loads((root / 'fork/wayvr-import.json').read_text())
for name, digest in meta['sha256'].items():
    actual = hashlib.sha256((root / 'server/wayvr/upstream' / name).read_bytes()).hexdigest()
    assert actual == meta['fork_sha256'].get(name, digest), f'Unrecorded WayVR change: {name}'
subprocess.run(['git', 'apply', '--reverse', '--check', '--directory=server/wayvr/upstream',
                'patches/wayvr/0001-managed-worker.patch'], cwd=root, check=True)
print(f"WayVR source pin {meta['head']}: {len(meta['sha256'])} files verified; integration patch matches")
