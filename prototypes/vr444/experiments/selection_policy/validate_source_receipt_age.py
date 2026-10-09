#!/usr/bin/env python3
"""Seal source-identity receipt-age diagnostics and full offline regressions."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('validate_source_age',HERE/'source_receipt_age.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    modules=(m,m.supply,m.supply.envelope,m.supply.oracle,m.cf,m.cf.frozen,m.cf.trace,m.cf.replay,m.cf.source,m.q,m.q.novelty,m.q.target,m.q.cardinality)
    paths=[Path(__file__)]+[Path(x.__file__) for x in modules]+list((ROOT/'validation/wivrn-codecs/tests').glob('test_*.py'))
    before={str(p.relative_to(ROOT)):sha(p) for p in paths}
    command=[sys.executable,'-m','unittest','discover','-s','validation/wivrn-codecs/tests']
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    if run.returncode:raise ValueError(run.stderr)
    if any(sha(ROOT/name)!=digest for name,digest in before.items()):raise ValueError('source-age validation source changed')
    result=dict(schema=1,status='pass',command=command,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr,
        sources_sha256=before,headset_operations=False,native_behavior_changed=False,latency_gate_changed=False,measured_speedup=False,
        scope='Known source receipt cannot reset on new encoded identity, explicit later-observed origin metadata, canonical multisource keys, missing/future/invalid receipts, independent host/client clock translation and complete existing Python suite.')
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__=='__main__':main()
