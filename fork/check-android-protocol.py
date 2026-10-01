#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""Compare actual ARM64 and PC protocol constants without running either binary."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess
import tempfile

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("compile_commands", type=Path, help="Android CMake compile_commands.json")
args = parser.parse_args()
root = Path(__file__).resolve().parent.parent
entries = json.loads(args.compile_commands.read_text())
entry = next(e for e in entries if e["file"].endswith("/client/spatial_source.cpp"))
original = shlex.split(entry["command"])
command = []
i = 0
while i < len(original):
    flag = original[i]
    if flag in ("-o", "-MF", "-MT", "-c"):
        i += 2
    elif flag in ("-MD", "-MMD"):
        i += 1
    else:
        command.append(flag)
        i += 1
objcopy = Path(command[0]).parent / "llvm-objcopy"
with tempfile.TemporaryDirectory(prefix="spatial-protocol-") as tmp:
    directory = Path(tmp)
    source = directory / "hash.cpp"
    source.write_text('#include "protocol_version.h"\nextern "C" const uint64_t spatial_hash '
                      '__attribute__((section(".spatial_protocol_hash"))) = wivrn::protocol_version;\n')
    common = ["-std=c++23", f"-I{root / 'common'}", f"-I{root / 'external'}"]
    subprocess.run(["c++", *common, "-c", str(source), "-o", str(directory / "pc.o")], check=True)
    subprocess.run([*command, "-fno-lto", "-c", str(source), "-o", str(directory / "arm.o")],
                   cwd=entry["directory"], check=True)
    values = []
    for platform in ("pc", "arm"):
        out = directory / f"{platform}.bin"
        subprocess.run([str(objcopy), f"--dump-section=.spatial_protocol_hash={out}", str(directory / f"{platform}.o")], check=True)
        value = out.read_bytes()
        if len(value) != 8:
            raise SystemExit("Unexpected protocol constant size")
        values.append(value)
    if values[0] != values[1]:
        raise SystemExit("PC and ARM64 protocol hashes differ")
    print(f"PC / ARM64 protocol match: {int.from_bytes(values[0], 'little'):016x}")
