#!/usr/bin/env python3
"""Seal source-age bracket regressions and the complete current Python suite."""
import argparse
import hashlib
import importlib.util
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
    spec=importlib.util.spec_from_file_location('validate_source_age_bound_module',HERE/'source_age_bound.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    age=m.age;cf=m.cf
    modules=(m,age,age.supply,age.supply.envelope,age.supply.oracle,cf,cf.frozen,cf.trace,cf.replay,cf.source,
        age.q,age.q.novelty,age.q.target,age.q.cardinality)
    paths=[Path(__file__)]+[Path(x.__file__) for x in modules]+list((ROOT/'validation/wivrn-codecs/tests').glob('test_*.py'))
    paths+=list((ROOT/'prototypes/vr444/experiments/compositor_qualification').glob('*.py'))
    before={str(p.relative_to(ROOT)):sha(p) for p in paths}
    command=[sys.executable,'-m','unittest','discover','-s','validation/wivrn-codecs/tests']
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    if run.returncode or not re.search(r'\nRan [1-9][0-9]* tests in [0-9.]+s\n\nOK\n?$',run.stderr):
        raise ValueError('nonempty unskipped Python suite required: '+run.stderr)
    if any(sha(ROOT/name)!=digest for name,digest in before.items()):raise ValueError('source-age bracket validation source changed')
    result=dict(schema=1,status='pass',command=command,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr,
        sources_sha256=before,headset_operations=False,native_behavior_changed=False,latency_gate_changed=False,measured_speedup=False,
        exhaustive_finite_path_populations=2048,
        scope='Componentwise age/nearest-rank monotonicity, exact strict threshold boundaries, source-age brackets over exhaustive finite path sets, pinned lower-bound basis and full existing Python regressions.')
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__=='__main__':main()
