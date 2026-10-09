#!/usr/bin/env python3
"""Bind target-admission controls/audits to the independently validated exact solver."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[4]
HERE = Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-receipt', type=Path, required=True)
    parser.add_argument('--solver', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    build = json.loads(args.build_receipt.read_text())
    if build.get('status') != 'pass' or sha(args.solver) != build.get('solver_sha256'):
        raise ValueError('exact solver differs from validated build')
    if any(sha(ROOT / name) != digest for name, digest in build['sources_sha256'].items()):
        raise ValueError('exact solver/auditor sources changed')
    paths = [Path(__file__), HERE/'target_admission.py', HERE/'phase_frontier.py', args.build_receipt, args.solver]
    paths += list((ROOT/'validation/wivrn-codecs/tests').glob('test_*.py'))
    before = {str(p.resolve()): sha(p) for p in paths}
    command = [sys.executable, '-m', 'unittest', 'discover', '-s', 'validation/wivrn-codecs/tests']
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
    if result.returncode:
        raise ValueError(result.stderr)
    if any(sha(name) != digest for name, digest in before.items()):
        raise ValueError('target-admission validation inputs changed')
    receipt = dict(schema=1, status='pass', command=command, returncode=result.returncode,
        stdout=result.stdout, stderr=result.stderr, sources_sha256=before,
        exact_solver_validation_sha256=sha(args.build_receipt), exact_solver_sha256=sha(args.solver),
        native_behavior_changed=False, headset_operations=False, measured_speedup=False,
        scope='Monotone target-ceiling transformation, 1024 all-edge equivalence populations, direct target admission and existing full offline suite; exact solver uses its preserved exhaustive sanitizer receipt.')
    with args.out.open('x') as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write('\n')
    print(result.stderr.strip().splitlines()[-3:])


if __name__ == '__main__':
    main()
