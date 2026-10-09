#!/usr/bin/env python3
"""Standalone sanitizer admission and read-only actual native TU syntax."""
import argparse,hashlib,json,shlex,subprocess,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
OVERLAY=ROOT/'validation/wivrn-codecs/integration/source-overlay'
NAMES=['common/codec_test/quality_sampler.h','client/decoder/test_decoder.cpp','client/decoder/decoder.h','client/scenes/stream_quality_capture.h','server/encoder/encoder_settings.cpp']
def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--native',type=Path,required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    assert a.native.is_absolute() and a.out.is_absolute() and not a.out.exists();a.out.mkdir()
    sources=[Path(__file__),HERE/'test.cpp',*[OVERLAY/n for n in NAMES]]
    before={str(p):digest(p) for p in sources};commands=[];toolchains={}
    def run(label,argv,cwd=None):
        started=time.monotonic();r=subprocess.run(argv,cwd=cwd,capture_output=True,timeout=90)
        command=dict(label=label,argv=argv,returncode=r.returncode,elapsed_s=time.monotonic()-started,stdout=r.stdout.decode(),stderr=r.stderr.decode());commands.append(command)
        (a.out/'partial.json').write_text(json.dumps(commands,indent=2)+'\n')
        if r.returncode:raise RuntimeError(label+': '+r.stderr.decode()[-3000:])
        return r
    binary=a.out/'admission-asan';compiler='/usr/bin/clang++';toolchains[compiler]=digest(compiler)
    run('asan-build',[compiler,'-std=c++23','-O1','-g','-fsanitize=address,undefined','-fno-omit-frame-pointer','-I'+str(OVERLAY/'common'),str(HERE/'test.cpp'),'-o',str(binary)])
    result=run('asan-admission',[str(binary),str(a.out/'choice.txt')]);assert result.stdout==b'64 strict sampler admission and file-reader checks passed\n' and not result.stderr
    vfs=a.out/'overlay.json';vfs.write_text(json.dumps({'version':0,'use-external-names':False,'roots':[{'type':'file','name':str(a.native/n),'external-contents':str(OVERLAY/n)} for n in NAMES]}))
    databases={};jobs=[('.cxx/Debug/2hd2e6j2/arm64-v8a/compile_commands.json','client/decoder/test_decoder.cpp','android-decoder'),('.cxx/Debug/2hd2e6j2/arm64-v8a/compile_commands.json','client/scenes/stream.cpp','android-stream'),('build/codec-server/compile_commands.json','server/encoder/encoder_settings.cpp','host-settings')]
    for db,name,label in jobs:
        path=a.native/db;databases[str(path)]=digest(path);rows=json.loads(path.read_bytes());row=next(r for r in rows if r['file']==str(a.native/name));args=row.get('arguments') or shlex.split(row['command']);base=[];i=0
        toolchains[args[0]]=digest(args[0])
        while i<len(args):
            if args[i] in ('-o','-MF','-MT','-MQ'):i+=2;continue
            if args[i] in ('-c','-MD','-MMD','-MP'):i+=1;continue
            base.append(args[i]);i+=1
        if label.startswith('host'):
            private=a.out/'host-settings.cpp';private.write_bytes((OVERLAY/name).read_bytes());base=[str(private) if arg==row['file'] else arg for arg in base]
            base+=['-iquote',str((a.native/name).parent),'-I'+str(OVERLAY/'common')]
        else:base+=['-ivfsoverlay',str(vfs)]
        for enabled in (True,False):
            argv=[x for x in base if not x.startswith('-DWIVRN_CODEC_TESTS')]+['-fsyntax-only']
            if enabled:argv+=['-DWIVRN_CODEC_TESTS=1']
            else:argv+=['-UWIVRN_CODEC_TESTS']
            run(label+('-on' if enabled else '-off'),argv,row['directory'])
    assert before=={str(p):digest(p) for p in sources} and databases=={p:digest(p) for p in databases}
    report=dict(schema=1,status='pass',source_sha256=before,sources_unchanged=True,native_compile_database_sha256=databases,toolchain_sha256=toolchains,binary_sha256={str(binary):digest(binary)},commands=commands,native_tree_mutated=False,native_link=False,headset_used=False,gpu_used=False,nearest_image_quality_measured=False,scope='ASan/UBSan admission and actual Android decoder/stream plus host settings ON/OFF syntax only')
    (a.out/'qualification.json').write_text(json.dumps(report,indent=2)+'\n');print(json.dumps(dict(status='pass',commands=len(commands))))
if __name__=='__main__':main()
