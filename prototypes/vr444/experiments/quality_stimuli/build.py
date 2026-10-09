#!/usr/bin/env python3
"""Local source/command/binary binding, with no runtime or GPU execution."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import time

HERE = Path(__file__).resolve().parent

def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p=argparse.ArgumentParser();p.add_argument('output',type=Path);p.add_argument('--sanitize',action='store_true');a=p.parse_args()
    if not a.output.is_absolute() or a.output.exists():raise ValueError('new absolute output directory required')
    compiler=shutil.which(os.environ.get('CXX','c++'))
    if not compiler:raise ValueError('CXX executable unavailable')
    a.output.mkdir();sources={name:sha(HERE/name) for name in ('app.cpp','pattern.hpp','test_transfer.cpp','build.py','build.sh')}
    flags=shlex.split(subprocess.check_output(['pkg-config','--cflags','--libs','openxr','vulkan','openssl'],text=True))
    base=[compiler,'-std=c++20','-O1' if a.sanitize else '-O2','-g','-Wall','-Wextra','-Werror','-Wno-missing-field-initializers']
    if a.sanitize:base+=['-fsanitize=address,undefined','-fno-omit-frame-pointer']
    commands=[]
    for source,output in [('app.cpp','quality-stimuli'),('test_transfer.cpp','test-transfer')]:
        argv=base+[str(HERE/source),'-o',str(a.output/output)]+flags;start=time.monotonic()
        with (a.output/(output+'.build.stdout')).open('wb') as stdout,(a.output/(output+'.build.stderr')).open('wb') as stderr:
            r=subprocess.run(argv,stdout=stdout,stderr=stderr,timeout=180)
        commands.append(dict(argv=argv,exit_code=r.returncode,wall_seconds=time.monotonic()-start))
        if r.returncode:raise RuntimeError('compile failed: '+output)
    if sources!={name:sha(HERE/name) for name in sources}:raise RuntimeError('source changed during build')
    receipt=dict(schema=1,source_directory=str(HERE),source_sha256=sources,compiler=str(Path(compiler).resolve()),
        compiler_sha256=sha(Path(compiler)),compiler_version=subprocess.check_output([compiler,'--version'],text=True),
        pkg_config_versions=subprocess.check_output(['pkg-config','--modversion','openxr','vulkan','openssl'],text=True).splitlines(),
        commands=commands,sanitizers=a.sanitize,binary_sha256={name:sha(a.output/name) for name in ('quality-stimuli','test-transfer')},
        log_sha256={f.name:sha(f) for f in sorted(a.output.glob('*.build.*'))},
        openxr_exercised=False,gpu_exercised=False,headset_exercised=False,
        scope='Local exact source/command/binary binding; no compiler/runtime reproducibility claim')
    (a.output/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')

if __name__=='__main__':main()
