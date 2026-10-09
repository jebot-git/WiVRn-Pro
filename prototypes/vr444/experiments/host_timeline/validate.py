#!/usr/bin/env python3
"""Seal host-timeline invariants and the complete offline Python suite."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    spec=importlib.util.spec_from_file_location('timeline_validation',HERE/'analyze.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    cf=module.cf
    paths=[Path(__file__),HERE/'analyze.py',*(Path(m.__file__) for m in (cf,cf.trace,cf.replay,cf.source,cf.frozen))]
    paths+=list((ROOT/'validation/wivrn-codecs/tests').glob('test_*.py'))
    before={str(path.relative_to(ROOT)):sha(path) for path in paths}
    command=[sys.executable,'-m','unittest','discover','-s','validation/wivrn-codecs/tests']
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    if run.returncode:raise ValueError(run.stderr)
    if any(sha(ROOT/name)!=digest for name,digest in before.items()):
        raise ValueError('host timeline validation source changed')
    result=dict(schema=1,status='pass',command=command,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr,
        sources_sha256=before,headset_operations=False,native_behavior_changed=False,measured_speedup=False,
        scope='Exact per-sample telescope, independent host/client clock translations, warm selection history versus all host latch history, missing/duplicate/regressive/unsupported source rejection, signed wake phase and existing full offline suite.')
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__=='__main__':main()
