#!/usr/bin/env python3
"""Offline source-bound overlay parity and harness regression; no native build/device."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
INTEGRATION = ROOT/'validation/wivrn-codecs/integration'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sources():
    files = [p for p in INTEGRATION.rglob('*') if p.is_file() and '__pycache__' not in p.parts
             and (p.suffix == '.py' or 'source-overlay' in p.parts)]
    files += list((ROOT/'validation/wivrn-codecs/tests').glob('*.py'))
    files += list((ROOT/'prototypes/vr444/experiments/pacer_recent_tail').glob('*.py'))
    files += [INTEGRATION/'integration.patch', INTEGRATION/'overlay-manifest.json', Path(__file__),
              ROOT/'prototypes/vr444/experiments/native_publication_integration/verify_overlay.py',
              ROOT/'prototypes/vr444/experiments/dual_goal_workflow/reconcile_overlay.py',
              ROOT/'prototypes/vr444/experiments/headset_review_smoke/native_campaign.py',
              ROOT/'prototypes/vr444/experiments/headset_review_smoke/test_native_campaign.py']
    return {str(p.relative_to(ROOT)): sha(p) for p in sorted(set(files))}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True, help='New output directory')
    args = parser.parse_args(); out = args.out.absolute(); out.mkdir(parents=True, exist_ok=False)
    before = sources(); records = []
    parity = out/'overlay-parity.json'
    commands = [
        [sys.executable, 'prototypes/vr444/experiments/native_publication_integration/verify_overlay.py',
         '--baseline-out', str(out/'before97'), '--out', str(parity)],
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'validation/wivrn-codecs/tests', '-v'],
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'prototypes/vr444/experiments/pacer_recent_tail',
         '-p', 'test_analyze.py', '-v'],
        [sys.executable, '-m', 'unittest', 'discover', '-s', 'prototypes/vr444/experiments/headset_review_smoke',
         '-p', 'test_native_campaign.py', '-v'],
    ]
    try:
        for i, command in enumerate(commands):
            result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, timeout=120)
            stem = f'{i:02d}'; stdout = out/(stem+'.stdout'); stderr = out/(stem+'.stderr')
            stdout.write_text(result.stdout); stderr.write_text(result.stderr)
            record = dict(argv=command, exit_code=result.returncode,
                          stdout=dict(path=stdout.name, sha256=sha(stdout)),
                          stderr=dict(path=stderr.name, sha256=sha(stderr)))
            records.append(record)
            if result.returncode:
                raise ValueError('offline command failed: '+str(command))
            if '-m' in command:
                match = re.search(r'\nRan ([1-9][0-9]*) tests? in [^\n]+\n\nOK\n?\Z', result.stderr)
                if match is None:
                    raise ValueError('test suite did not finish a nonempty unskipped population')
                record['tests'] = int(match.group(1))
        overlay = json.loads(parity.read_bytes())
        if overlay['status'] != 'pass' or overlay['overlay_files'] != 97 or sources() != before:
            raise ValueError('parity/population/source snapshot failed')
        receipt = dict(schema=1, status='pass', source_sha256=before, commands=records,
                       python_tests=sum(r.get('tests', 0) for r in records),
                       overlay_parity=dict(path=parity.name, sha256=sha(parity), files=97),
                       source_unchanged=True, native_build=False, device_operations=False,
                       graphics_test=False, performance_measured=False,
                       latency_qualified=False, quality_qualified=False, promotion_eligible=False)
    except Exception as exc:
        receipt = dict(schema=1, status='fail', source_sha256=before, commands=records, error=str(exc))
        (out/'qualification.json').write_text(json.dumps(receipt, indent=2)+'\n')
        raise
    (out/'qualification.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(dict(status='pass', python_tests=receipt['python_tests'], overlay_files=97)))


if __name__ == '__main__':
    main()
