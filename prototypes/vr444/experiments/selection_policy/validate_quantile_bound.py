#!/usr/bin/env python3
"""Bind quantile search and full offline tests to two preserved exact solvers."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[4]
HERE=Path(__file__).resolve().parent


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for kind in ('encoded','source'):
        parser.add_argument('--'+kind+'-solver',type=Path,required=True)
        parser.add_argument('--'+kind+'-build-receipt',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    paths=[Path(__file__),HERE/'quantile_bound.py']
    builds={}
    for kind in ('encoded','source'):
        solver=getattr(args,kind+'_solver');receipt=getattr(args,kind+'_build_receipt')
        build=json.loads(receipt.read_text())
        if build.get('status')!='pass' or sha(solver)!=build.get('solver_sha256'):
            raise ValueError('preserved solver binary differs')
        for name,digest in build['sources_sha256'].items():
            if sha(ROOT/name)!=digest:
                raise ValueError('preserved solver source differs: '+name)
            paths.append(ROOT/name)
        paths.extend((solver,receipt))
        builds[kind]=dict(solver_sha256=sha(solver),build_receipt_sha256=sha(receipt))
    paths+=list((ROOT/'validation/wivrn-codecs/tests').glob('test_*.py'))
    before={str(path.resolve()):sha(path) for path in paths}
    command=[sys.executable,'-m','unittest','discover','-s','validation/wivrn-codecs/tests']
    run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
    if run.returncode:
        raise ValueError(run.stderr)
    if any(sha(path)!=digest for path,digest in before.items()):
        raise ValueError('quantile validation input changed')
    result=dict(schema=1,status='pass',command=command,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr,
        sources_sha256=before,solver_builds=builds,exhaustive_quantile_path_populations=512,
        worst_age_deletion_populations=512,headset_operations=False,native_behavior_changed=False,
        measured_speedup=False,scope='Exact integer quantile search, strict boundary/adversarial tests and complete existing offline suite; preserved exact solver receipts separately qualify C++ optimizers.')
    with args.out.open('x') as stream:
        json.dump(result,stream,indent=2,allow_nan=False);stream.write('\n')
    print(run.stderr.strip().splitlines()[-3:])


if __name__=='__main__':
    main()
