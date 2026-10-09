#!/usr/bin/env python3
"""Bind offline target-envelope checks to reviewed source snapshots."""
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
    paths = [Path(__file__), HERE / 'target_envelope.py', HERE / 'availability_oracle.py', HERE / 'counterfactual.py',
             HERE / 'reanalyze_trace.py', HERE.parent / 'compositor_freshness/analyze.py',
             ROOT / 'validation/wivrn-codecs/tests/test_target_envelope.py',
             ROOT / 'validation/wivrn-codecs/tests/test_availability_oracle.py',
             ROOT / 'validation/wivrn-codecs/integration/candidate_trace.py',
             ROOT / 'validation/wivrn-codecs/integration/selection_replay.py']
    def hashes():
        return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    before = hashes()
    run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if run.returncode or hashes() != before:
        raise RuntimeError('tests failed or sources changed: ' + run.stderr)
    result = dict(schema=1, status='pass', command=command, cwd=str(ROOT), returncode=run.returncode,
        stdout=run.stdout, stderr=run.stderr, sources_sha256=before,
        existing_envelope_behavior_unchanged=True, headset_operations=False, native_behavior_changed=False,
        measured_speedup=False, latency_gate_evaluated=False,
        scope='Offline target-ceiling sensitivity and exact source joins; graph solver retains exhaustive regression coverage.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__ == '__main__':
    main()
