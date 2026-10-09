#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check both WPC1 implementations using an already prepared corpus."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'packet_compression'))
from wrapper import Native,encode,decode,MAX
p=argparse.ArgumentParser();p.add_argument('binary');p.add_argument('corpus',type=Path)
a=p.parse_args();native=Native();count=0
with tempfile.TemporaryDirectory(prefix='wpc-cross-') as d:
    d=Path(d)
    for src in sorted(a.corpus.glob('*.payload')):
        raw=src.read_bytes()
        for block in [MAX,16384,1100]:
            py=encode(raw,native,1,block);(d/'python.wpc').write_bytes(py)
            subprocess.run([a.binary,'decode',str(d/'python.wpc'),str(d/'native.raw')],check=True)
            assert (d/'native.raw').read_bytes()==raw
            subprocess.run([a.binary,'encode',str(src),str(d/'native.wpc'),str(block)],check=True)
            wire=(d/'native.wpc').read_bytes()
            assert decode(wire,native)==raw and wire==py
            count+=1
assert count>0
print(json.dumps(dict(status='pass',profiles=count,python_to_native_exact=True,native_to_python_exact=True,identical_wire=True)))
