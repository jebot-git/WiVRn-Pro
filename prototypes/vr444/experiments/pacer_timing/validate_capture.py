#!/usr/bin/env python3
"""Record pure offline tests of pacer capture controls and exact source joins."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    command = ['python3', '-m', 'unittest', 'discover', '-s', 'validation/wivrn-codecs/tests']
    run = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    if run.returncode:
        raise RuntimeError(run.stderr)
    files = [Path(__file__), HERE/'join_source.py', HERE/'analyze.py', HERE.parent/'compositor_freshness/analyze.py']
    files += [ROOT / ('validation/wivrn-codecs/' + name) for name in (
        'integration/native_compare.py', 'integration/latency_policy.py',
        'tests/test_native_compare.py', 'tests/test_latency_policy.py', 'tests/test_pacer_source_join.py')]
    result = dict(schema=1, status='pass', command=command, cwd=str(ROOT), returncode=run.returncode,
        stdout=run.stdout, stderr=run.stderr,
        sources_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        native_build=False, device_commands_executed=False, headset_operations=False,
        measured_speedup=False, latency_gate_evaluated=False,
        scope='Pure launch-option/preflight/file-evaluation/identity-join tests; no runtime acquisition.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__ == '__main__':
    main()
