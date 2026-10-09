#!/usr/bin/env python3
"""Bind local-clock timeline regression checks to exact source files."""
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
    result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError(result.stderr)
    files = [Path(__file__), HERE / 'analyze.py', ROOT / 'validation/wivrn-codecs/tests/test_client_timeline.py']
    files += [ROOT / ('validation/wivrn-codecs/integration/' + name) for name in
              ('candidate_trace.py', 'selection_replay.py', 'latency_runtime.py', 'native_metrics.py')]
    receipt = dict(schema=1, status='pass', command=command, cwd=str(ROOT), returncode=result.returncode,
        stdout=result.stdout, stderr=result.stderr,
        sources_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        headset_operations=False, measured_speedup=False, qualification_changed=False,
        scope='Offline interval/manifest/identity correctness, without runtime execution.')
    with args.out.open('x') as stream:
        json.dump(receipt, stream, indent=2)
        stream.write('\n')
    print(result.stderr.strip().splitlines()[-3:])


if __name__ == '__main__':
    main()
