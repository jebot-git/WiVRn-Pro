#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Independent manual wire vectors, no codec library or encoder dependency."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib


def rrf(w, h, mode, body):
    header = struct.pack('<4sBBBBIIIII', b'RRF1', 1, mode, 0, 0, w, h, len(body), w*h*3, zlib.crc32(body))
    return header + struct.pack('<I', zlib.crc32(header)) + body


def frame(w, h, blocks):
    cursor = 32+8*len(blocks)
    table = b''
    for block in blocks:
        table += struct.pack('<II', cursor, len(block))
        cursor += len(block)
    header = struct.pack('<4sBBBBIIIII', b'RRB1', 1, 4, 0, 0, w, h, len(blocks), cursor, zlib.crc32(table))
    return header + struct.pack('<I', zlib.crc32(header)) + table + b''.join(blocks)


def cases():
    rgb = bytes((12, 34, 56)); rgba = rgb+b'\xff'
    # 3 literals + offset3 match40 + final5 =48 decoded bytes.
    plain = b'\x3f'+rgb+b'\x03\x00\x15\x50'+bytes((34, 56, 12, 34, 56))
    # First RGB row + one zero literal, offset1 match39, final5 zeroes.
    up = b'\x4f'+rgb+b'\x00\x01\x00\x14\x50'+b'\x00'*5
    last = bytes((2, 3, 4)); tail = rrf(1, 1, 0, last)
    raw = bytes(range(153))
    return [('plain-tail', frame(1, 17, [rrf(1, 16, 1, plain), tail]), rgba*16+last+b'\xff'),
            ('up-tail-reset', frame(1, 17, [rrf(1, 16, 2, up), tail]), rgba*16+last+b'\xff'),
            ('raw-up-reset', frame(1, 32, [rrf(1, 16, 0, last*16), rrf(1, 16, 2, up)]), (last+b'\xff')*16+rgba*16),
            ('raw-odd', frame(3, 17, [rrf(3, 16, 0, raw[:144]), rrf(3, 1, 0, raw[144:])]), b''.join(raw[i:i+3]+b'\xff' for i in range(0,len(raw),3)))]


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('output',type=Path);p.add_argument('--decoder',type=Path);args=p.parse_args()
    args.output.mkdir(parents=True,exist_ok=True);records=[]
    for name,wire,rgba in cases():
        packet=args.output/(name+'.rrb');pixels=args.output/(name+'.rgba');packet.write_bytes(wire);pixels.write_bytes(rgba)
        if args.decoder:subprocess.run([str(args.decoder),str(packet),str(pixels)],check=True)
        records.append(dict(name=name,wire_bytes=len(wire),rgba_bytes=len(rgba),wire_sha256=hashlib.sha256(wire).hexdigest(),rgba_sha256=hashlib.sha256(rgba).hexdigest()))
    (args.output/'manifest.json').write_text(json.dumps(dict(schema=1,independent_manual_lz4=True,cases=records),indent=2)+'\n')


if __name__=='__main__':main()
