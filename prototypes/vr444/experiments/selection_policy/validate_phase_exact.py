#!/usr/bin/env python3
"""Bind readiness-event search to exhaustive phase tests and exact solvers."""
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
    controls=[Path(__file__),HERE/'phase_exact.py',HERE/'phase_exact_run.py',HERE/'validate_receipt_floor.py']
    before={str(p.resolve()):sha(p) for p in controls}
    with tempfile.TemporaryDirectory(prefix='exact-phase-validation-') as temporary:
        path=Path(temporary)/'receipt-floor-validation.json'
        command=[sys.executable,str(HERE/'validate_receipt_floor.py')]
        for kind in ('encoded','source'):
            command+=['--'+kind+'-solver',str(getattr(args,kind+'_solver')),'--'+kind+'-build-receipt',str(getattr(args,kind+'_build_receipt'))]
        command+=['--out',str(path)]
        run=subprocess.run(command,capture_output=True,text=True,timeout=120)
        if run.returncode:raise ValueError(run.stderr)
        result=json.loads(path.read_text())
    if any(sha(name)!=digest for name,digest in before.items()):raise ValueError('exact-phase validation source changed')
    result['sources_sha256'].update(before)
    result.update(exact_phase_control_command=command,exhaustive_integer_phase_path_populations=1024,
        exhaustive_edge_change_populations=512,exact_phase_scope='Every integer phase independently enumerated in1024 bounded models,512 readiness-edge populations, earliest ties, malformed controls, nonmonotone oracle rejection and adjacent-threshold seed requalification.')
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(result['stderr'].strip().splitlines()[-3:])


if __name__=='__main__':main()
