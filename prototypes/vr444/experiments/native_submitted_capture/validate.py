#!/usr/bin/env python3
"""Offline contract sanitizer and Android translation-unit checks; no devices."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
OVERLAY = ROOT/'validation/wivrn-codecs/integration/source-overlay'

def binding(path):
    path = Path(path)
    data = path.read_bytes()
    return {'path': str(path), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--native-root',type=Path,required=True)
    p.add_argument('--compile-commands',type=Path,required=True)
    p.add_argument('--simdjson-root',type=Path,required=True)
    p.add_argument('--out',type=Path,required=True)
    args=p.parse_args()
    native=args.native_root.resolve()
    files=sorted(x for x in OVERLAY.rglob('*') if x.is_file())
    inputs=files+[Path(__file__),HERE/'contract_test.cpp',args.compile_commands,
                  args.simdjson_root/'singleheader/simdjson.h',args.simdjson_root/'singleheader/simdjson.cpp']
    before=[binding(x) for x in inputs]
    database=json.loads(args.compile_commands.read_text())
    checks=[]
    def run(name,argv,cwd):
        q=subprocess.run(argv,cwd=cwd,capture_output=True,text=True,timeout=180)
        checks.append(dict(name=name,argv=argv,cwd=str(cwd),exit=q.returncode,stdout=q.stdout,stderr=q.stderr))
        if q.returncode or q.stderr:
            raise RuntimeError(name+' failed: '+q.stderr)
        return q.stdout
    with tempfile.TemporaryDirectory(prefix='submitted-capture-check-') as work:
        work=Path(work)
        vfs=work/'vfs.json'
        vfs.write_text(json.dumps({'version':0,'roots':[{'type':'file','name':str(native/x.relative_to(OVERLAY)),'external-contents':str(x)} for x in files if x.suffix!='.spv']}))
        binary=work/'contract'
        compile_argv=['clang++','-std=c++20','-O1','-g','-Wall','-Wextra','-Werror','-fsanitize=address,undefined','-fno-omit-frame-pointer',
                      '-I'+str(OVERLAY/'common'),'-I'+str(args.simdjson_root/'singleheader'),str(HERE/'contract_test.cpp'),
                      str(args.simdjson_root/'singleheader/simdjson.cpp'),'-o',str(binary)]
        run('ASan/UBSan contract compile',compile_argv,ROOT)
        output=run('ASan/UBSan config/budget/bounds/exclusive-write contract',[str(binary)],ROOT)
        if output!='PASS submitted capture contract: 38 rejections; 24 budgets; absolute frame alignment; bounds; no-overwrite evidence\n':
            raise ValueError('unexpected contract success output')
        binary_binding=binding(binary)
        for relative in ['client/scenes/stream.cpp','client/xr/swapchain.cpp']:
            candidates=[x for x in database if Path(x['file'])==native/relative]
            if len(candidates)!=1: raise ValueError('missing/ambiguous translation unit: '+relative)
            entry=candidates[0]
            original=entry.get('arguments') or shlex.split(entry['command'])
            for enabled in [False,True]:
                argv=[];skip=False
                for value in original:
                    if skip: skip=False;continue
                    if value in ('-o','-MF','-MT','-MQ'):skip=True;continue
                    if value in ('-c','-MD','-MMD') or value.startswith('-DWIVRN_CODEC_TESTS'):continue
                    argv.append(value)
                if enabled:argv.append('-DWIVRN_CODEC_TESTS=1')
                argv+=['-fsyntax-only','-ivfsoverlay',str(vfs),'-fno-color-diagnostics']
                run(relative+' Android syntax diagnostics '+str(enabled),argv,entry['directory'])
        stream=(OVERLAY/'client/scenes/stream.cpp').read_text()
        render=stream[stream.index('void scenes::stream::render('):stream.index('void scenes::stream::exit()')]
        ordered=['defoveator->defoveate(', 'test_quality_capture->record(', 'command_buffer.end()', 'test_quality_capture->before_submit()',
                 'queue.lock()->submit(', 'test_quality_capture->observe(', 'swapchain.release()', 'render_end()', 'test_quality_capture->finish(']
        locations=[render.index(x) for x in ordered]
        if locations!=sorted(locations):raise ValueError('capture lifetime/submission source order changed')
        checks.append(dict(name='actual render capture-source ordering',status='pass',runtime_proof=False,order=ordered))
        after=[binding(x) for x in inputs]
        if before!=after:raise ValueError('source/control changed during checks')
    result=dict(schema=1,status='pass',checks=checks,inputs=before,contract_binary=binary_binding,
                device_operations=False,vulkan_copy_executed=False,xr_submission_executed=False,
                native_linked_build=False,quality_qualified=False,latency_qualified=False,
                scope='CPU contract sanitizer and actual Android stream/swapchain translation-unit syntax ON/OFF; runtime capture remains unexecuted.')
    with args.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print('PASS submitted capture offline contract and four Android syntax arms')

if __name__=='__main__':main()
