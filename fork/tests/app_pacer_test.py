#!/usr/bin/env python3
"""Exercise the production pacer with simulated time, without an XR session."""
# SPDX-License-Identifier: GPL-3.0-or-later
from pathlib import Path
import argparse
import json
import resource
import shlex
import subprocess

root = Path(__file__).resolve().parents[2]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('build', nargs='?', type=Path, default=root / 'build/server')
parser.add_argument('--sanitize', action='store_true')
parser.add_argument('--compiler', help='override the configured C++ compiler')
options = parser.parse_args()
build = options.build.resolve()
entry = next(c for c in json.loads((build / 'compile_commands.json').read_text())
             if c['file'].endswith('/app_pacer.cpp'))
args = shlex.split(entry['command'])
if options.compiler:
    args[0] = options.compiler
    if 'clang' in options.compiler:
        args += ['-Wno-unknown-warning-option']
index = args.index('-o')
del args[index:index + 2]
args.remove('-c')
args.remove(entry['file'])
# Reuse dependency include paths from the configured build, but compile this
# checkout's production source (the build may belong to another worktree).
output = root / 'build/native-tests/app-pacer-test'
output.parent.mkdir(parents=True, exist_ok=True)
args += ['-UNDEBUG', '-fno-fast-math', '-I' + str(root / 'server/driver'),
         str(root / 'server/driver/app_pacer.cpp'), str(root / 'fork/tests/app_pacer_test.cpp'),
         str(build / '_deps/monado-build/src/xrt/auxiliary/util/libaux_util.a'),
         str(build / '_deps/monado-build/src/xrt/auxiliary/os/libaux_os.a'),
         '-ldl', '-o', str(output)]
if options.sanitize:
    args += ['-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer']
subprocess.run(args, cwd=entry['directory'], check=True)
resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
subprocess.run([str(output)], check=True)
