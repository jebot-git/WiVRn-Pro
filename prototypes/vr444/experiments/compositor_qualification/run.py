#!/usr/bin/env python3
"""Separate historical integrity from an explicitly requested fresh Python suite."""
import argparse
import importlib.util
import json
from pathlib import Path
import re
import subprocess
import sys

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location('compositor_history_runner',HERE/'history.py')
history=importlib.util.module_from_spec(spec);spec.loader.exec_module(history)
ROOT=history.ROOT
PREFIXES=('validation/wivrn-codecs','prototypes/vr444/experiments/selection_policy',
    'prototypes/vr444/experiments/pacer_timing','prototypes/vr444/experiments/client_timeline',
    'prototypes/vr444/experiments/host_timeline','prototypes/vr444/experiments/compositor_qualification')


def snapshot(root):
    root=Path(root).resolve();result={}
    for prefix in PREFIXES:
        directory=root/prefix
        if not directory.is_dir():raise ValueError('required source directory missing: '+prefix)
        for path in sorted(directory.rglob('*')):
            if '__pycache__' in path.parts:continue
            if path.is_symlink():raise ValueError('source snapshot cannot follow a symlink: '+str(path))
            if path.is_file():result[str(path.relative_to(root))]=history.digest(path.read_bytes())
    if not result:raise ValueError('empty current source snapshot')
    return result


def python_tests(run):
    if run.returncode!=0:raise ValueError('fresh Python suite failed: '+run.stderr)
    match=re.search(r'\nRan ([1-9][0-9]*) tests? in [0-9.]+s\n\nOK\n?$',run.stderr)
    if match is None:raise ValueError('fresh Python suite must execute tests and finish with plain OK, without skips')
    return int(match.group(1))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=('history','python'),default='history')
    parser.add_argument('--out',type=Path,required=True);args=parser.parse_args()
    out=args.out.resolve()
    if any(out.is_relative_to(ROOT/prefix) for prefix in PREFIXES):parser.error('write output outside the snapshotted source directories')
    before=snapshot(ROOT);catalog_path=HERE/'catalog.json';catalog_raw=catalog_path.read_bytes()
    archived=history.verify(json.loads(catalog_raw),history.GitBlobs(ROOT))
    stages=[dict(stage='historical_git_blob_integrity',result=archived)]
    if args.mode=='python':
        command=[sys.executable,'-m','unittest','discover','-s','validation/wivrn-codecs/tests','-v']
        run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,timeout=120)
        count=python_tests(run)
        stages.append(dict(stage='fresh_python_regressions',status='pass',command=command,
            tests_executed=count,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr))
    expected=['historical_git_blob_integrity']+(['fresh_python_regressions'] if args.mode=='python' else [])
    if [s['stage'] for s in stages]!=expected:raise ValueError('requested suite stage omitted or duplicated')
    if snapshot(ROOT)!=before or catalog_path.read_bytes()!=catalog_raw:raise ValueError('current sources changed during suite')
    result=dict(schema=1,status='completed_with_historical_source_gap' if archived['known_source_gaps'] else 'pass',
        mode=args.mode,stages=stages,catalog_sha256=history.digest(catalog_raw),current_files_sha256=before,
        current_source_snapshot_unchanged=True,fresh_python_executed=args.mode=='python',
        fresh_cpp_tests=False,fresh_sanitizers=False,fresh_android_compile=False,fresh_native_build=False,
        acquisition_files_reverified=False,remote_refs_verified=False,headset_operations=False,
        performance_measurement=False,latency_gate_pass=False,promotion_eligible=False,
        scope='Archived artifacts and source bindings are separate from fresh Python execution. The explicit historical plot-source gap remains visible.')
    with out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(dict(status=result['status'],mode=args.mode,approaches=archived['approach_count'],
        artifacts=archived['artifacts_verified'],historical_gaps=len(archived['known_source_gaps']),
        fresh_tests=stages[-1].get('tests_executed',0))))


if __name__=='__main__':main()
