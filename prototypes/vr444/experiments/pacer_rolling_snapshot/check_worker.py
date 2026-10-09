#!/usr/bin/env python3
import argparse,hashlib,importlib.util,json,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];SOURCE=ROOT/'validation/wivrn-codecs/integration/source-overlay/server/compositor'
def module(name,path):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
old=module('rolling_stubs',HERE.parent/'pacer_recent_tail/worker_check.py');analyzer=module('rolling_analyzer',HERE.parent/'pacer_recent_tail/analyze.py')
p=argparse.ArgumentParser();p.add_argument('--sanitizer',choices=['address,undefined','thread'],required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();records=[]
with tempfile.TemporaryDirectory(prefix='pacer-rolling-worker-')as folder:
 d=Path(folder)
 for name,data in old.STUBS.items():q=d/name;q.parent.mkdir(parents=True,exist_ok=True);q.write_text(data)
 cmd=['clang++','-std=c++23','-O1','-g','-ffast-math','-DWIVRN_CODEC_TESTS=1','-fsanitize='+a.sanitizer,'-fno-omit-frame-pointer','-pthread','-I',str(d),'-I',str(SOURCE),str(SOURCE/'pacer.cpp'),str(HERE/'native_feedback_test.cpp'),'-o',str(d/'test')]
 for argv in [cmd,[str(d/'test'),str(d/'capture')]]:
  r=subprocess.run(argv,capture_output=True,text=True,timeout=60);records.append(dict(argv=argv,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr));
  if r.returncode or r.stderr:raise RuntimeError(records[-1])
 raw=(d/'capture-tail.jsonl').read_bytes();audit=analyzer.evaluate([json.loads(x)for x in raw.splitlines()]);assert audit['status']=='pass' and audit['feedback_bank']=='rolling'
 files=[Path(__file__),HERE/'native_feedback_test.cpp',Path(old.__file__),Path(analyzer.__file__),*[SOURCE/n for n in ('pacer.cpp','pacer.h','codec_pacer_tail.h','codec_pacer_trace.h')]]
 result=dict(schema=1,status='pass',sanitizer=a.sanitizer,commands=records,source_sha256={str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest()for x in files},tail_audit=audit,actual_native_feedback_and_worker=True,external_runtime_api_stubs=True,device_used=False,performance_measured=False)
 with a.out.open('x')as f:json.dump(result,f,indent=2);f.write('\n')
print('actual rolling feedback '+a.sanitizer+' passed')
