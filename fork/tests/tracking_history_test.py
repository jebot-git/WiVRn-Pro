#!/usr/bin/env python3
"""Compile tracking regressions using the configured production dependencies."""
import argparse
import json
from pathlib import Path
import shlex
import subprocess

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('build', nargs='?', type=Path, default=root / 'build/server')
args = parser.parse_args()
build = args.build.resolve()
entry = next(c for c in json.loads((build / 'compile_commands.json').read_text())
             if c['file'].endswith('/app_pacer.cpp'))
command = shlex.split(entry['command'])
index = command.index('-o')
del command[index:index + 2]
command.remove('-c')
command.remove(entry['file'])
output = root / 'build/native-tests/tracking-history-test'
output.parent.mkdir(parents=True, exist_ok=True)
command += ['-UNDEBUG', '-fno-fast-math', '-I' + str(root / 'server/driver'),
            str(root / 'fork/tests/tracking_history_test.cpp'),
            str(root / 'common/crypto.cpp'), str(root / 'common/smp.cpp'),
            '-lcrypto', '-o', str(output)]
subprocess.run(command, cwd=entry['directory'], check=True)
subprocess.run([str(output)], check=True)
