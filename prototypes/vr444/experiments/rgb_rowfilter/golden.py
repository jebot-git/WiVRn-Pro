#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Independent tiny wire vectors with manually authored LZ4 blocks; no C++ encoder."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib


def frame(width, height, mode, body):
    header = struct.pack("<4sBBBBIIIII", b"RRF1", 1, mode, 0, 0, width, height,
                         len(body), width * height * 3, zlib.crc32(body))
    assert len(header) == 28
    return header + struct.pack("<I", zlib.crc32(header)) + body


def cases():
    rgb = bytes((12, 34, 56))
    rgba = rgb + b"\xff"
    # Three literals; offset3 copies40 bytes; five mandatory trailing literals.
    block_plain = b"\x3f" + rgb + b"\x03\x00\x15\x50" + bytes((34, 56, 12, 34, 56))
    # 13 literals (first RGB row and one zero); offset1 copies30 zeroes; last5.
    block_up = b"\xdf" + rgb * 4 + b"\0" + b"\x01\x00\x0b\x50" + b"\0" * 5
    return [("raw-one", frame(1, 1, 0, rgb), rgba),
            ("plain-four", frame(4, 4, 1, block_plain), rgba * 16),
            ("up-four", frame(4, 4, 2, block_up), rgba * 16),
            ("raw-odd", frame(3, 1, 0, bytes(range(9))), b"".join(bytes(range(i, i+3))+b"\xff" for i in range(0, 9, 3)))]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("output", type=Path)
    p.add_argument("--decoder", type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, wire, rgba in cases():
        packet, pixels = args.output / (name + ".rrf"), args.output / (name + ".rgba")
        packet.write_bytes(wire)
        pixels.write_bytes(rgba)
        if args.decoder:
            subprocess.run([str(args.decoder), str(packet), str(pixels)], check=True)
        records.append(dict(name=name, wire_bytes=len(wire), rgba_bytes=len(rgba),
                            wire_sha256=hashlib.sha256(wire).hexdigest(), rgba_sha256=hashlib.sha256(rgba).hexdigest()))
    (args.output / "manifest.json").write_text(json.dumps(dict(schema=1, independent_manual_lz4=True, cases=records), indent=2)+"\n")


if __name__ == "__main__":
    main()
