#!/usr/bin/env python3
"""Record offline oracle regression checks and their source hashes."""
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
    run = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, timeout=120)
    if run.returncode:
        raise RuntimeError(run.stderr)
    files = [Path(__file__), HERE / 'availability_oracle.py', HERE / 'counterfactual.py', HERE / 'reanalyze_trace.py',
             ROOT / 'validation/wivrn-codecs/tests/test_availability_oracle.py',
             ROOT / 'validation/wivrn-codecs/integration/candidate_trace.py',
             ROOT / 'validation/wivrn-codecs/integration/selection_replay.py',
             HERE.parent / 'compositor_freshness/analyze.py']
    result = dict(schema=1, status='pass', command=command, cwd=str(ROOT), returncode=run.returncode,
        stdout=run.stdout, stderr=run.stderr,
        sources_sha256={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files},
        exhaustive_random_graphs=256, objectives_per_graph=2,
        exhaustive_quantile_graphs=64, percentiles_per_graph=3,
        measured_speedup=False, headset_operations=False, native_sources_modified=False,
        scope='Offline optimistic count-bound solver correctness; no runtime timing claim.')
    with args.out.open('x') as stream:
        json.dump(result, stream, indent=2)
        stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__ == '__main__':
    main()
