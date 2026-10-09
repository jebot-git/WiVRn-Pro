#!/usr/bin/env python3
"""CPU-only qualification; never invokes a GPU, device runner or network."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[4]
INTEGRATION=ROOT/'validation/wivrn-codecs/integration'
sys.path.insert(0,str(INTEGRATION))
import original_reference_job as adapter


def sha(raw):return hashlib.sha256(raw).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    a.out.mkdir(parents=True,exist_ok=False)
    files=[INTEGRATION/n for n in ('original_reference_job.py','original_reference_compare.py','source_mapping.py','submitted_reference_job.py','submitted_capture.py','research_quality.py')]
    helper=INTEGRATION/'original_source_pixels.py'
    if helper.exists():files.append(helper)
    files.extend([Path(__file__),ROOT/'validation/wivrn-codecs/tests/test_original_reference.py',ROOT/'validation/wivrn-codecs/tests/test_original_source_pixels.py',ROOT/'prototypes/vr444/experiments/submitted_image_replay/capture.py'])
    before={str(f.relative_to(ROOT)):sha(f.read_bytes()) for f in files}
    start=time.monotonic()
    tests=subprocess.run([sys.executable,'-m','unittest','discover','-s','validation/wivrn-codecs/tests','-p','test_original*.py','-v'],cwd=ROOT,capture_output=True,timeout=30)
    (a.out/'tests.stdout').write_bytes(tests.stdout);(a.out/'tests.stderr').write_bytes(tests.stderr)
    if tests.returncode or not tests.stderr.endswith(b'\nOK\n'):raise ValueError('Python regression failed')
    spec=importlib.util.spec_from_file_location('existing_replay',ROOT/'prototypes/vr444/experiments/submitted_image_replay/capture.py')
    replay=importlib.util.module_from_spec(spec);spec.loader.exec_module(replay)
    receipt=replay.historical();name,expected=next((n,s) for n,s in receipt['binary_sha256'].items() if Path(n).name=='replay')
    binary=Path(name).read_bytes()
    if sha(binary)!=expected:raise ValueError('qualified host parser binary changed')
    checks=[]
    with tempfile.TemporaryDirectory(prefix='sub15-original-parser-') as directory:
        temp=Path(directory);exe=temp/'replay';exe.write_bytes(binary);exe.chmod(0o500)
        for width,height in ((4,4),(1800,1980)):
            wire=bytearray(struct.pack('<6I',0x31504c52,1,width,height,43,2))
            for eye in (0,1):
                adapter.identity_axis(width);adapter.identity_axis(height)
                wire.extend(struct.pack('<6I',width,height,1,1,0,0))
                wire.extend(struct.pack('<8i20f',0,0,width,height,0,0,0,0,*([1.]*4+[0.]*16)))
                wire.extend(struct.pack('<2I',width,height));wire.extend(bytes([13+eye,67,129,255])*(width*height))
            for kind,data,valid in [('identity',bytes(wire),True),('missing-right-tail',bytes(wire[:-1]),False),('trailing-byte',bytes(wire)+b'\0',False)]:
                path=temp/'job.rlp';path.write_bytes(data)
                r=subprocess.run([str(exe),str(path),'--cpu-only'],capture_output=True,timeout=10)
                if valid:
                    if r.returncode or r.stderr or json.loads(r.stdout)!=dict(job_valid=True,eyes=2,gpu_used=False):raise ValueError('valid identity job rejected')
                elif r.returncode==0:raise ValueError('invalid identity job accepted')
                checks.append(dict(width=width,height=height,case=kind,accepted=valid,returncode=r.returncode,input_sha256=sha(data),stdout_sha256=sha(r.stdout),stderr_sha256=sha(r.stderr)))
    after={str(f.relative_to(ROOT)):sha(f.read_bytes()) for f in files}
    if after!=before:raise ValueError('qualification source changed')
    result=dict(schema=1,status='pass',scope='Python synthetic control tests and qualified host RLP1 CPU parser only',
                sources_sha256=before,python_stdout_sha256=sha(tests.stdout),python_stderr_sha256=sha(tests.stderr),
                qualified_renderer_commit=replay.QUALIFIED,host_parser_sha256=expected,parser_checks=checks,
                elapsed_seconds=time.monotonic()-start,headset_used=False,gpu_used=False,
                actual_source_mapping_acquisition_used=False,original_to_submitted_quality_verified=False,full_goal_qualified=False)
    (a.out/'qualification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(dict(status='pass',parser_cases=len(checks),gpu_used=False)))
if __name__=='__main__':main()
