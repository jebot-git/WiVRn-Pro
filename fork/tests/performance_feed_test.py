#!/usr/bin/env python3
"""Build isolated native publisher tests using the configured server include paths."""
from pathlib import Path
import json, shlex, subprocess
root=Path(__file__).resolve().parents[2]
entry=next(c for c in json.loads((root/'build/server/compile_commands.json').read_text()) if c['file'].endswith('/performance_feed.cpp'))
args=shlex.split(entry['command']); index=args.index('-o'); del args[index:index+2]; args.remove('-c')
args += ['-UNDEBUG','-fno-fast-math','-pthread','-I'+str(root/'server/driver'),str(root/'fork/tests/performance_feed_test.cpp'),str(root/'build/server/common/libwivrn-common.a'),'-lcrypto','-o','/tmp/wivrn-performance-feed-test']
subprocess.run(args,cwd=entry['directory'],check=True)
subprocess.run(['/tmp/wivrn-performance-feed-test'],check=True)
print('Native telemetry: deduplication, reordering, latency stages, publication, permissions and cleanup passed')
