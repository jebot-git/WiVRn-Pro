#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Independent tiny RPT1 wire fixtures, constructed without the C++ encoder."""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import zlib


def rgb(value):
    return value.to_bytes(4, "little")[:3]


def frame(width, height, mode, payload):
    # One partial 16x16 tile. Header and grammar are authored independently.
    body = struct.pack("<HBB", len(payload), mode, 0) + payload
    header = struct.pack("<4sHHIIIII", b"RPT1", 1, 16, width, height, 1, len(body), zlib.crc32(body))
    return header + struct.pack("<I", zlib.crc32(header)) + body


def fixtures():
    palette = [0xFF112233, 0xFFADCDEF, 0xFF80FF01, 0xFF05A0E9, 0xFFCC9977]
    yield "raw", 3, 2, 0, b"".join(rgb(palette[i % 5]) for i in range(6)), [palette[i % 5] for i in range(6)]
    yield "constant", 3, 2, 1, rgb(palette[0]), [palette[0]] * 6
    for name, width, height, mode, colors, bits in (("palette2", 3, 2, 2, 2, 1),
                                                  ("palette4", 5, 3, 3, 3, 2),
                                                  ("palette16", 5, 3, 4, 5, 4)):
        indices = [i % colors for i in range(width * height)]
        packed = sum(index << (i * bits) for i, index in enumerate(indices))
        payload = bytes([colors]) + b"".join(rgb(c) for c in palette[:colors])
        payload += packed.to_bytes((len(indices) * bits + 7) // 8, "little")
        yield name, width, height, mode, payload, [palette[i] for i in indices]
    runs = [(3, palette[0]), (7, palette[1]), (5, palette[2])]
    yield "runs", 5, 3, 5, b"".join(bytes([n - 1]) + rgb(c) for n, c in runs), [c for n, c in runs for _ in range(n)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("output", type=Path)
    parser.add_argument("--verify", type=Path, help="compiled export executable")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    records = []
    for name, width, height, mode, payload, pixels in fixtures():
        wire = frame(width, height, mode, payload)
        rgba = b"".join(p.to_bytes(4, "little") for p in pixels)
        wp, rp = args.output / (name + ".rpt"), args.output / (name + ".rgba")
        wp.write_bytes(wire)
        rp.write_bytes(rgba)
        if args.verify:
            subprocess.run([str(args.verify.resolve()), "verify", str(wp), str(rp)], check=True, capture_output=True)
        records.append(dict(name=name, width=width, height=height, mode=mode,
                            rpt=wp.name, rgba=rp.name,
                            rpt_sha256=hashlib.sha256(wire).hexdigest(),
                            rgba_sha256=hashlib.sha256(rgba).hexdigest()))
    result = dict(schema=1, independent_of_cpp_encoder=True, cases=records,
                  cpp_verified=args.verify is not None)
    (args.output / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(dict(pass_=True, cases=len(records), cpp_verified=args.verify is not None)))


if __name__ == "__main__":
    main()
