#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Manual independent plane/delta/filter wire vectors; no codec library."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib


def frame(w,h,mode,layout,body):
    header=struct.pack('<4sBBBBIIIII',b'RCP1',1,mode,layout,0,w,h,len(body),w*h*3,zlib.crc32(body))
    return header+struct.pack('<I',zlib.crc32(header))+body


def planes(a,b,c):
    # Independent constant16-byte planes. Last sequence has5 literal bytes;
    # last match starts at output33, at least12 bytes before the end at48.
    return bytes((0x1b,a,1,0,0x1b,b,1,0,0x16,c,1,0,0x50))+bytes((c,))*5


def cases():
    rgba=bytes((12,34,56,255))*16
    # Each first row is four bytes; each later row is zero residual. First two
    # domains use five literals and an11-zero match; last domain is16 literals.
    up=b''.join(bytes((0x57,))+bytes((v,))*4+bytes((0,1,0)) for v in (34,234))
    up+=bytes((0xf0,1))+bytes((22,))*4+b'\x00'*12
    plain=bytes((0x3f,34,234,22,3,0,21,0x50,234,22,34,234,22))
    return [('planes-plain',frame(4,4,1,1,planes(12,34,56)),rgba),
            ('green-planes-plain',frame(4,4,1,3,planes(34,234,22)),rgba),
            ('green-planes-up',frame(4,4,2,3,up),rgba),
            ('green-interleaved',frame(4,4,1,2,plain),rgba),
            ('raw-odd',frame(3,1,0,0,bytes(range(9))),b''.join(bytes(range(i,i+3))+b'\xff' for i in range(0,9,3)))]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--decoder',type=Path);a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    records=[]
    for name,wire,rgba in cases():
        packet=a.output/(name+'.rcp');pixels=a.output/(name+'.rgba');packet.write_bytes(wire);pixels.write_bytes(rgba)
        if a.decoder:subprocess.run([str(a.decoder),str(packet),str(pixels)],check=True)
        records.append(dict(name=name,wire_bytes=len(wire),rgba_bytes=len(rgba),wire_sha256=hashlib.sha256(wire).hexdigest(),rgba_sha256=hashlib.sha256(rgba).hexdigest()))
    (a.output/'manifest.json').write_text(json.dumps(dict(schema=1,independent_manual_lz4=True,cases=records),indent=2)+'\n')


if __name__=='__main__':main()
