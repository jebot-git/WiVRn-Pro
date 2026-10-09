#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build the independent ASan/UBSan byte oracle and bind its actual dependencies."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
LZ=HERE.parent/'native_lz4'
REF=ROOT/'validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444'
SOURCES=[HERE/'native.cpp',HERE/'build.py',HERE.parent/'rowblocks_workers/workers.hpp',
    HERE.parent/'rgb_rowblocks/rowblocks.hpp',HERE.parent/'rgb_rowfilter/rowfilter.hpp',
    HERE.parent/'rgb_rowfilter/simd.hpp',LZ/'wpc.hpp',LZ/'vendor/lz4.c',LZ/'vendor/lz4.h',REF/'codec.cpp',REF/'codec.hpp']

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args();out=args.out.resolve();out.mkdir(parents=True,exist_ok=True)
    before={str(p.relative_to(ROOT)):sha(p) for p in SOURCES}
    cc,cxx=shutil.which('clang'),shutil.which('clang++')
    if not cc or not cxx:raise ValueError('clang and clang++ required')
    flags=['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-MMD']
    commands=[
        [cc,*flags,'-MF',str(out/'lz4.d'),'-c',str(LZ/'vendor/lz4.c'),'-o',str(out/'lz4.o')],
        [cxx,'-std=c++20','-pthread','-Wall','-Wextra','-Werror',*flags,'-I',str(LZ),'-I',str(REF),'-MF',str(out/'native.d'),'-c',str(HERE/'native.cpp'),'-o',str(out/'native.o')],
        [cxx,'-std=c++20',*flags,'-MF',str(out/'codec.d'),'-c',str(REF/'codec.cpp'),'-o',str(out/'codec.o')],
        [cxx,'-pthread','-fsanitize=address,undefined',str(out/'native.o'),str(out/'codec.o'),str(out/'lz4.o'),'-o',str(out/'native')]]
    for command in commands:subprocess.run(command,check=True)
    dependencies=set()
    for name in ('lz4.d','native.d','codec.d'):
        text=(out/name).read_text().replace('\\\n',' ')
        dependencies.update(Path(p).resolve() for p in text.split(':',1)[1].split())
    if not dependencies.issubset({p.resolve() for p in SOURCES}):
        raise ValueError('unbound compiler dependency: '+repr(dependencies-{p.resolve() for p in SOURCES}))
    if any(sha(ROOT/name)!=digest for name,digest in before.items()):raise ValueError('source changed during build')
    receipt=dict(schema=1,status='pass',sanitizers=['address','undefined'],commands=commands,
        compiler_versions={compiler:subprocess.check_output([compiler,'--version'],text=True) for compiler in (cc,cxx)},
        sources_sha256=before,compiler_dependencies=sorted(str(p.relative_to(ROOT)) for p in dependencies),
        binary=str(out/'native'),binary_sha256=sha(out/'native'))
    (out/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(status='pass',binary=receipt['binary'],bound_sources=len(before))))

if __name__=='__main__':main()
