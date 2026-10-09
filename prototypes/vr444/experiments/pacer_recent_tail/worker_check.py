#!/usr/bin/env python3
"""Compile actual pacer source with isolated external API stubs; no device/runtime."""
import argparse,json,subprocess,tempfile,hashlib
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3];SOURCE=ROOT/'validation/wivrn-codecs/integration/source-overlay/server/compositor'
STUBS={
'wivrn_packets.h':'''#pragma once
#include <cstdint>
using XrTime=int64_t;using XrDuration=int64_t;
namespace wivrn::from_headset {struct feedback {int times_displayed=1;int64_t blitted=0,frame_index=0,received_from_decoder=0,displayed=0;int stream_index=0;};}
''',
'main/comp_target.h':'''#pragma once
enum comp_target_timing_point { COMP_TARGET_TIMING_POINT_WAKE_UP,COMP_TARGET_TIMING_POINT_BEGIN,COMP_TARGET_TIMING_POINT_SUBMIT_BEGIN,COMP_TARGET_TIMING_POINT_SUBMIT_END};
''',
'util/u_var.h':'''#pragma once
#include <array>
#include <cassert>
inline constexpr int64_t U_TIME_1MS_IN_NS=1000000;
struct u_var_draggable_f32 {float val,step,min,max;};
inline void u_var_add_root(void*,const char*,bool){} inline void u_var_add_draggable_f32(void*,void*,const char*){} inline void u_var_remove_root(void*){}
''',
'driver/clock_offset.h':'''#pragma once
namespace wivrn {struct clock_offset {int64_t from_headset(int64_t value)const{return value;}};}
''',
'os/os_time.h':'''#pragma once
inline int64_t os_monotonic_get_ns(){return 1000000000;}
''',
'util/u_debug.h':'''#pragma once
#define DEBUG_GET_ONCE_FLOAT_OPTION(name,env,value) inline float debug_get_float_option_##name(){return value;}
'''}
def main():
 p=argparse.ArgumentParser();p.add_argument('--sanitizer',choices=['address,undefined','thread'],required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();checks=[]
 with tempfile.TemporaryDirectory(prefix='pacer-worker-')as d:
  d=Path(d)
  for n,s in STUBS.items():q=d/n;q.parent.mkdir(parents=True,exist_ok=True);q.write_text(s)
  cmd=['clang++','-std=c++23','-O1','-g','-ffast-math','-DWIVRN_CODEC_TESTS=1','-fsanitize='+a.sanitizer,'-fno-omit-frame-pointer','-pthread','-I',str(d),'-I',str(SOURCE),str(SOURCE/'pacer.cpp'),str(HERE/'native_worker_test.cpp'),'-o',str(d/'test')]
  for argv in [cmd,[str(d/'test')]]:
   r=subprocess.run(argv,capture_output=True,text=True,timeout=60);checks.append(dict(argv=argv,exit=r.returncode,stdout=r.stdout,stderr=r.stderr));
   if r.returncode or r.stderr:raise RuntimeError(str(checks[-1]))
  files=[Path(__file__),HERE/'native_worker_test.cpp',*[SOURCE/n for n in ['pacer.cpp','pacer.h','codec_pacer_tail.h','codec_pacer_trace.h']]]
  result=dict(status='pass',sanitizer=a.sanitizer,checks=checks,sources_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()for p in files},actual_native_worker_source=True,external_api_stubs=True,actual_runtime=False)
  with a.out.open('x')as f:json.dump(result,f,indent=2);f.write('\n')
 print('actual native worker '+a.sanitizer+' passed')
if __name__=='__main__':main()
