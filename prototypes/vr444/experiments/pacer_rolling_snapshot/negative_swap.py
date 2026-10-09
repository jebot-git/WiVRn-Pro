#!/usr/bin/env python3
"""A disposable old-swap counterfactual must fail the actual-feedback ring oracle."""
import argparse,hashlib,importlib.util,json,resource,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];SOURCE=ROOT/'validation/wivrn-codecs/integration/source-overlay/server/compositor'
s=importlib.util.spec_from_file_location('rolling_negative_stubs',HERE.parent/'pacer_recent_tail/worker_check.py');old=importlib.util.module_from_spec(s);s.loader.exec_module(old)
p=argparse.ArgumentParser();p.add_argument('--out',type=Path,required=True);a=p.parse_args()
original=(SOURCE/'pacer.cpp').read_bytes();needle=b'std::copy(frame_times.begin(),frame_times.end(),frame_times_compute.begin());';assert original.count(needle)==1
mutated=original.replace(needle,b'std::swap(frame_times,frame_times_compute);')
records=[]
with tempfile.TemporaryDirectory(prefix='pacer-rolling-negative-')as folder:
 d=Path(folder)
 for name,data in old.STUBS.items():q=d/name;q.parent.mkdir(parents=True,exist_ok=True);q.write_text(data)
 (d/'pacer.cpp').write_bytes(mutated)
 cmd=['clang++','-std=c++23','-O1','-g','-ffast-math','-DWIVRN_CODEC_TESTS=1','-fsanitize=address,undefined','-fno-omit-frame-pointer','-pthread','-I',str(d),'-I',str(SOURCE),str(d/'pacer.cpp'),str(HERE/'native_feedback_test.cpp'),'-o',str(d/'test')]
 r=subprocess.run(cmd,capture_output=True,text=True,timeout=60);assert r.returncode==0 and not r.stderr
 records.append(dict(argv=cmd,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
 resource.setrlimit(resource.RLIMIT_CORE,(0,0))
 argv=[str(d/'test'),str(d/'capture')];r=subprocess.run(argv,capture_output=True,text=True,timeout=30)
 assert r.returncode!=0 and 'same(p.frame_times[i],reference[i])' in r.stderr and 'ERROR: AddressSanitizer' not in r.stderr and 'runtime error:' not in r.stderr
 records.append(dict(argv=argv,exit_code=r.returncode,stdout=r.stdout,stderr=r.stderr))
files=[Path(__file__),HERE/'native_feedback_test.cpp',Path(old.__file__),*[SOURCE/n for n in ('pacer.cpp','pacer.h','codec_pacer_trace.h','codec_pacer_tail.h')]]
result=dict(schema=1,status='pass',semantic_negative_control='old alternating swap cannot satisfy cumulative rolling ring contract',modified_native_source_sha256=hashlib.sha256(mutated).hexdigest(),source_sha256={str(x.relative_to(ROOT)):hashlib.sha256(x.read_bytes()).hexdigest()for x in files},commands=records,original_native_source_unchanged=(SOURCE/'pacer.cpp').read_bytes()==original,device_used=False)
with a.out.open('x')as f:json.dump(result,f,indent=2);f.write('\n')
print('old-swap semantic negative rejected by actual-feedback oracle')
