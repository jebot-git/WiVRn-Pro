#!/usr/bin/env python3
"""Seal endpoint necessary-bound arithmetic and no-false-rejection checks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    paths=[Path(__file__),HERE/'endpoint_envelope.py']+list((ROOT/'validation/wivrn-codecs/tests').glob('test_*.py'))
    # Bind all current local Python dependencies, policy data and helper code;
    # bytecode caches and historical JSON analysis outputs are not executable.
    for prefix in ('validation/wivrn-codecs/integration','prototypes/vr444/experiments/selection_policy',
                   'prototypes/vr444/experiments/pacer_timing','prototypes/vr444/experiments/client_timeline',
                   'prototypes/vr444/experiments/host_timeline','prototypes/vr444/experiments/compositor_qualification'):
        paths+=list((ROOT/prefix).glob('*.py'))
    paths += [ROOT/'validation/wivrn-codecs/integration'/name for name in ('LATENCY-POLICY.json','CPU-LATENCY-TARGET.json')]
    before={str(p.relative_to(ROOT)):sha(p) for p in paths}
    command=[sys.executable,'-m','unittest','discover','-s','validation/wivrn-codecs/tests']
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    if run.returncode or not re.search(r'\nRan [1-9][0-9]* tests in [0-9.]+s\n\nOK\n?$',run.stderr):
        raise ValueError('nonempty unskipped Python suite required: '+run.stderr)
    if any(sha(ROOT/name)!=digest for name,digest in before.items()):raise ValueError('endpoint validation source changed')
    result=dict(schema=1,status='pass',command=command,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr,
        sources_sha256=before,headset_operations=False,native_behavior_changed=False,latency_gate_changed=False,
        performance_measurement=False,promotion_eligible=False,
        scope='512 exhaustive ordered references, 256 exhaustive small chain populations, strict percentile and cadence boundaries, same-deadline alternatives cannot be reused, readiness/target checks and exact-type rejection; full Python regressions.')
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__=='__main__':main()
