#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Fresh pinned Python suites; optional execution of two bound native test binaries."""
import argparse
import io
import json
from pathlib import Path
import re
import subprocess
import sys
import tarfile
import tempfile
import catalog as c

PYTHON_STAGES=(('datagram-xor','test_transport.py'),('transport-queue','test_replay.py'),
    ('xor-interleave','test_transport.py'),('codec-transport-budget','test_model.py'),
    ('latency-wire-frontier','test_frontier.py'),('rowblock-stream','test_transport.py'),
    ('rowblock-stream-fec','test_transport.py'),('packet-window-fec','test_transport.py'))

def snapshot(root):
    result={}
    for path in sorted(Path(root).rglob('*')):
        if '__pycache__' in path.parts:continue
        c.need(not path.is_symlink(),'source snapshot contains symlink')
        if path.is_file():result[str(path.relative_to(root))]=c.digest(path.read_bytes())
    c.need(result,'empty source snapshot');return result

def test_count(result):
    c.need(result.returncode==0,'Python suite failed: '+result.stderr)
    match=re.search(r'\nRan ([1-9][0-9]*) tests? in [0-9.]+s\n\nOK\n?$',result.stderr)
    c.need(match is not None,'Python suite must report nonzero tests, plain OK and no skips')
    return int(match[1])

def extract(raw,destination):
    with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
        members=archive.getmembers()
        for member in members:
            c.relative(member.name.rstrip('/'))
            c.need(member.isdir() or member.isfile(),'archive cannot contain links/devices')
            c.need(member.name.startswith('prototypes/') or member.isdir() and member.name=='prototypes','archive outside source prefix')
        archive.extractall(destination,filter='data')

def python_stage(name,filename,git):
    commit,directory=next((commit,c.PREFIX+d) for candidate,commit,d in c.MILESTONES if candidate==name)
    raw=subprocess.check_output(['git','-C',str(c.ROOT),'archive',commit,'--','prototypes/vr444/experiments'],timeout=60)
    with tempfile.TemporaryDirectory(prefix='sub15-pinned-python-') as tmp:
        root=Path(tmp);extract(raw,root);before=snapshot(root)
        # Exact extracted bytes are checked against their immutable Git objects,
        # before execution, not merely against a newly generated archive digest.
        expected=git.files(commit,'prototypes/vr444/experiments')
        c.need(set(before)==set(expected),'extracted source inventory differs')
        for path,sha in before.items():c.need(c.digest(git.blob(commit,path))==sha,'extracted source differs from Git')
        command=[sys.executable,'-m','unittest','-v',filename]
        result=subprocess.run(command,cwd=root/directory,text=True,capture_output=True,timeout=120)
        count=test_count(result);c.need(snapshot(root)==before,'pinned extracted source changed during tests')
    return dict(stage=name,commit=commit,status='pass',tests=count,command=command,
        source_archive_sha256=c.digest(raw),extracted_files=len(before),extracted_sources_unchanged=True,
        stdout=result.stdout,stderr=result.stderr,sockets_opened=False,headset_operations=False)

def native_stages(directory,git):
    name,commit,d=next(row for row in c.MILESTONES if row[0]=='rowblocks-band-views')
    receipt_path=c.PREFIX+d+'/results/qualification.json';raw=git.blob(commit,receipt_path);receipt=json.loads(raw)
    c.need(receipt.get('status')=='pass','passing bound native receipt required')
    for path,sha in receipt['source_sha256'].items():
        c.need(c.digest(git.blob(commit,path))==sha,'native archived dependency differs')
    rows=[]
    for binary,field in (('test','asan_ubsan'),('test-tsan','thread_sanitizer')):
        path=(Path(directory)/binary).resolve();expected=receipt['binary_sha256'][binary]
        c.need(c.digest(path.read_bytes())==expected,'native binary is not the pinned historical test executable')
        run=subprocess.run([str(path)],capture_output=True,text=True,timeout=180)
        c.need(run.returncode==0 and not run.stderr,'native correctness/sanitizer failure')
        result=json.loads(run.stdout);baseline=receipt[field]
        c.need(result.get('pass') is True,'native test did not pass')
        for key in ('exact_canonical_stereo_cases','direct_rgb_oracle_cases','differential_corruption_cases',
                    'reuse_batches','max_block_jobs','max_pixels_per_eye'):
            c.need(type(result.get(key)) is int and result[key]==baseline[key],'native coverage differs: '+key)
        c.need(type(result.get('rejections')) is int and result['rejections']>0,'native rejection coverage absent')
        c.need(c.digest(path.read_bytes())==expected,'native binary changed during execution')
        rows.append(dict(stage='fresh_bound_'+binary,status='pass',binary=str(path),binary_sha256=expected,
            build_receipt_commit=commit,build_receipt_path=receipt_path,build_receipt_sha256=c.digest(raw),
            result=result,rebuilt=False,network_accessed=False,headset_operations=False))
    return rows

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--native-band-directory',type=Path)
    args=parser.parse_args();out=args.out.resolve();c.need(not out.is_relative_to(c.HERE),'receipt must be outside runner source')
    before=snapshot(c.HERE);git=c.Git(c.ROOT);history=c.verify(json.loads((c.HERE/'catalog.json').read_text()),git)
    stages=[python_stage(name,filename,git) for name,filename in PYTHON_STAGES]
    if args.native_band_directory:stages+=native_stages(args.native_band_directory,git)
    expected=[name for name,_ in PYTHON_STAGES]+(['fresh_bound_test','fresh_bound_test-tsan'] if args.native_band_directory else [])
    c.need([s['stage'] for s in stages]==expected,'requested stage missing or duplicated')
    c.need(snapshot(c.HERE)==before,'runner/catalog changed during qualification')
    result=dict(schema=1,status='completed_with_historical_snapshot_differences' if history['recorded_snapshot_differences'] else 'pass',
        history=history,stages=stages,fresh_python_tests=sum(s.get('tests',0) for s in stages),
        native_bound_executions=2 if args.native_band_directory else 0,runner_sha256=before,
        sources_unchanged=True,new_native_build=False,android_runtime_tested=False,headset_operations=False,
        performance_measurement=False,latency_gate_pass=False,promotion_eligible=False,
        limitations=['Eight Python suites run from distinct immutable approach snapshots. They are not merged production integration tests.',
            'Optional native executables are byte-bound historical ASan/UBSan and TSan tests; no rebuild or compiler environment reproduction is claimed.',
            'Loopback/native interop is historical only in this runner; it does not launch sockets or repeat archived performance measurements.',
            'Historical source differences remain explicit regardless of fresh selected-suite outcomes.'])
    with out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(dict(status=result['status'],python_tests=result['fresh_python_tests'],native_executions=result['native_bound_executions'])))

if __name__=='__main__':main()
