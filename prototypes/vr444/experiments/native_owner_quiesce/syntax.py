#!/usr/bin/env python3
"""Read-only native Android TU validation through an isolated Clang VFS overlay."""
import argparse,hashlib,json,shlex,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    p=argparse.ArgumentParser();p.add_argument('--commands',type=Path,required=True);p.add_argument('--native-root',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    overlay=ROOT/'validation/wivrn-codecs/integration/source-overlay'
    files=[x for x in overlay.rglob('*') if x.is_file()]
    sources={str(x.relative_to(ROOT)):sha(x) for x in files};sources[str(Path(__file__).relative_to(ROOT))]=sha(Path(__file__))
    row=next(r for r in json.loads(a.commands.read_bytes()) if r['file']==str(a.native_root/'client/application.cpp'))
    args=row.get('arguments') or shlex.split(row['command']);kept=[];i=0
    while i<len(args):
        if args[i] in ('-o','-MF','-MT','-MQ'):i+=2;continue
        if args[i] in ('-c','-MD','-MMD'):i+=1;continue
        kept.append(args[i]);i+=1
    checks=[]
    with tempfile.TemporaryDirectory(prefix='sub15-owner-vfs-') as tmp:
        vfs=Path(tmp)/'overlay.json';vfs.write_text(json.dumps({'version':0,'roots':[{'type':'file','name':str(a.native_root/x.relative_to(overlay)),'external-contents':str(x)} for x in files]}))
        for enabled in (True,False):
            argv=[x for x in kept if not x.startswith('-DWIVRN_CODEC_TESTS')]+['-fsyntax-only','-ivfsoverlay',str(vfs)]
            if enabled:argv.append('-DWIVRN_CODEC_TESTS=1')
            run=subprocess.run(argv,cwd=row['directory'],capture_output=True,text=True,timeout=90)
            if run.returncode:raise RuntimeError(run.stderr)
            checks.append(dict(enabled=enabled,argv=argv,returncode=run.returncode,stdout=run.stdout,stderr=run.stderr,compiler_sha256=sha(Path(argv[0]))))
    if sources!={**{str(x.relative_to(ROOT)):sha(x) for x in files},str(Path(__file__).relative_to(ROOT)):sha(Path(__file__))}:raise RuntimeError('source changed')
    a.output.write_text(json.dumps(dict(status='pass',scope='Actual Android application TU enabled/disabled syntax only; no linked native executable',compile_commands_sha256=sha(a.commands),sources_sha256=sources,checks=checks,native_files_mutated=False,headset_used=False),indent=2)+'\n')
    print('actual Android application TU: enabled + disabled syntax pass')
if __name__=='__main__':main()
