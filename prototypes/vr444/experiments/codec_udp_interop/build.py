#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Build isolated host helpers and bind exact compiler invocations to binaries."""
import datetime
import hashlib
import json
from pathlib import Path
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def inputs():
    names=('codec_udp_interop/build.sh','codec_udp_interop/build.py','codec_udp_interop/relay.cpp','codec_udp_interop/decode.cpp',
        'udp_batch/probe.cpp','udp_offload/offload.hpp','rgb_rowblocks/rowblocks.hpp','rgb_rowfilter/rowfilter.hpp','rgb_rowfilter/simd.hpp',
        'native_lz4/wpc.hpp','native_lz4/vendor/lz4.h','native_lz4/vendor/lz4.c')
    paths=[HERE.parent/name for name in names]
    ref=ROOT/'validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444'
    return paths+[ref/'codec.hpp',ref/'codec.cpp']


def main():
    if len(sys.argv)!=2:raise ValueError('build requires output directory')
    out=Path(sys.argv[1]).resolve();out.mkdir(parents=True,exist_ok=True)
    before={str(f.relative_to(ROOT)):sha(f) for f in inputs()}
    lz=HERE.parent/'native_lz4';ref=ROOT/'validation/wivrn-codecs/integration/source-overlay/common/codec_test/vr444'
    flags=['-std=c++20','-Wall','-Wextra','-Werror','-Wno-missing-field-initializers','-I',str(lz),'-I',str(ref)]
    san=['-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer']
    commands=[['clang',*san,'-c',str(lz/'vendor/lz4.c'),'-o',str(out/'lz4-test.o')],
        ['clang++',*flags,*san,str(HERE/'relay.cpp'),'-o',str(out/'relay')],
        ['clang++',*flags,*san,str(HERE/'decode.cpp'),str(ref/'codec.cpp'),str(out/'lz4-test.o'),'-o',str(out/'decode')]]
    for argv in commands:subprocess.run(argv,check=True,timeout=120)
    if any(sha(ROOT/name)!=digest for name,digest in before.items()):raise ValueError('build source changed')
    receipt=dict(schema=1,status='pass',configuration='host_address_undefined',created_utc=datetime.datetime.now(datetime.timezone.utc).isoformat(),
        successful_compiler_argv=commands,source_sha256=before,source_unchanged=True,binary_sha256={name:sha(out/name) for name in ('relay','decode')},
        compiler_versions={name:subprocess.check_output([name,'--version'],text=True).splitlines()[0] for name in ('clang','clang++')})
    (out/'build-receipt.json').write_text(json.dumps(receipt,indent=2)+'\n')
    print(json.dumps(dict(status='pass',binaries=str(out),build_receipt_sha256=sha(out/'build-receipt.json'))))


if __name__=='__main__':main()
