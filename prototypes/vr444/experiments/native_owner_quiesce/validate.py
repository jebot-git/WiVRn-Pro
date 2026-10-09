#!/usr/bin/env python3
"""Extract actual app retirement methods; compile/run bounded standalone regressions."""
import argparse,hashlib,json,os,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
APP=ROOT/'validation/wivrn-codecs/integration/source-overlay/client/application.cpp'
HEADER=ROOT/'validation/wivrn-codecs/integration/source-overlay/client/scene.h'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def extract(text,name):
    start=text.index('void application::'+name+'()');brace=text.index('{',start);depth=0
    for i in range(brace,len(text)):
        depth += (text[i]=='{')-(text[i]=='}')
        if depth==0:return text[start:i+1]
    raise ValueError('unterminated method')
def methods(text):
    # Only the actual initial retirement scope of cleanup is linked; spatial,
    # action and JNI cleanup requires the native application and is not mocked.
    cleanup=extract(text,'cleanup');cleanup=cleanup[:cleanup.index('\tspatial.reset();')]+'}\n'
    return cleanup+'\n'+extract(text,'pop_scene')
def main():
    p=argparse.ArgumentParser();p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    sources={str(x.relative_to(ROOT)):sha(x) for x in (APP,HEADER,HERE/'ownership_test.cpp',Path(__file__))}
    text=APP.read_text();template=(HERE/'ownership_test.cpp').read_text();checks=[]
    with tempfile.TemporaryDirectory(prefix='sub15-owner-retirement-') as tmp:
        tmp=Path(tmp);src=tmp/'test.cpp';src.write_text(template.replace('@@METHODS@@',methods(text)))
        for sanitizer in ('address,undefined','thread'):
            binary=tmp/('test-'+sanitizer.replace(',','-'))
            argv=['clang++','-std=c++20','-O1','-g','-pthread','-DWIVRN_CODEC_TESTS=1','-fsanitize='+sanitizer,str(src),'-o',str(binary)]
            build=subprocess.run(argv,capture_output=True,text=True,timeout=40)
            if build.returncode:raise RuntimeError(build.stderr)
            run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=15)
            expected='owner_retirement: 105 scenes, callback references joined, stack unlocked, device alive\n'
            if run.returncode or run.stdout!=expected or run.stderr:raise RuntimeError(str(run))
            checks.append(dict(sanitizer=sanitizer,argv=argv,binary_sha256=sha(binary),stdout=run.stdout,stderr=run.stderr,returncode=run.returncode))
        # Prior exact source is an independent regression control: no hooks means
        # the owner returns without joining the callback-retained scene.
        old=subprocess.check_output(['git','show','1b3541c:validation/wivrn-codecs/integration/source-overlay/client/application.cpp'],cwd=ROOT,text=True)
        src.write_text(template.replace('@@METHODS@@',methods(old)))
        binary=tmp/'negative';subprocess.run(['clang++','-std=c++20','-O1','-pthread','-DWIVRN_CODEC_TESTS=1',str(src),'-o',str(binary)],check=True,capture_output=True,timeout=40)
        run=subprocess.run([str(binary)],capture_output=True,text=True,timeout=5)
        if run.returncode==0 or 'prepared==i+1' not in run.stderr:raise RuntimeError('old code failed to exercise the ownership regression')
        checks.append(dict(name='historical no-hook negative control',returncode=run.returncode,stderr=run.stderr))
    assert sources=={str(x.relative_to(ROOT)):sha(x) for x in (APP,HEADER,HERE/'ownership_test.cpp',Path(__file__))}
    a.output.write_text(json.dumps(dict(status='pass',sources_sha256=sources,checks=checks,native_application_executed=False,gpu_executed=False,scope='Actual app method bodies with modeled scene callbacks; separate native TU syntax and integration required'),indent=2)+'\n')
    print('owner retirement: ASan/UBSan + TSan + historical regression control pass')
if __name__=='__main__':main()
