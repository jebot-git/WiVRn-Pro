#!/usr/bin/env python3
"""Bind receipt-floor controls to full offline tests and preserved solvers."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

HERE=Path(__file__).resolve().parent


def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for kind in ('encoded','source'):
        parser.add_argument('--'+kind+'-solver',type=Path,required=True)
        parser.add_argument('--'+kind+'-build-receipt',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    controls=[Path(__file__),HERE/'receipt_floor.py',HERE/'quantile_bound.py',HERE/'validate_quantile_bound.py']
    before={str(p.resolve()):sha(p) for p in controls}
    with tempfile.TemporaryDirectory(prefix='receipt-floor-validation-') as temporary:
        path=Path(temporary)/'quantile-validation.json'
        command=[sys.executable,str(HERE/'validate_quantile_bound.py')]
        for kind in ('encoded','source'):
            command+=['--'+kind+'-solver',str(getattr(args,kind+'_solver')),'--'+kind+'-build-receipt',str(getattr(args,kind+'_build_receipt'))]
        command+=['--out',str(path)]
        run=subprocess.run(command,capture_output=True,text=True,timeout=120)
        if run.returncode:raise ValueError(run.stderr)
        result=json.loads(path.read_text())
    if any(sha(name)!=digest for name,digest in before.items()):raise ValueError('receipt-floor validation source changed')
    result['sources_sha256'].update(before)
    result.update(receipt_floor_control_command=command,receipt_floor_random_domination_populations=1024,
        receipt_floor_scope='Zero-delay participating-eye floors including alpha; receipt/range and mode control rejection; unchanged bounded advance default and exact quantile/cardinality regressions.')
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(result['stderr'].strip().splitlines()[-3:])


if __name__=='__main__':main()
