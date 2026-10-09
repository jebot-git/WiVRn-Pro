#!/usr/bin/env python3
"""Bounded source-extracted native foveation prediction. Never a quality pass."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import re
import subprocess
import time
import numpy as np

ROOT=Path(__file__).resolve().parents[4]
SOURCE=ROOT/'validation/wivrn-codecs/integration/source-overlay/server/compositor/foveation.cpp'
QUALITY=ROOT/'validation/wivrn-codecs/integration/research_quality.py'

def sha(data):return hashlib.sha256(data).hexdigest()
def need(v,m):
    if not v:raise ValueError(m)
def load_quality():
    s=importlib.util.spec_from_file_location('quality_prediction_oracle',QUALITY);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def extract(raw):
    s=raw.decode()
    first=s[s.index('static double defoveate('):s.index('static bool is_zero_quat(')]
    second=s[s.index('static float angles_to_center('):s.index('\nnamespace wivrn\n{',s.index('static void fill_param_2d('))]
    pre='\n'.join('#include <'+x+'>' for x in ['algorithm','cassert','bit','cmath','cstdint','iostream','optional','tuple','vector'])+'\n'
    main=r'''
int main(){
 float angle,left,right;unsigned encoded,source;
 while(std::cin>>angle>>left>>right>>encoded>>source){
  auto finite=[](float x){return (std::bit_cast<uint32_t>(x)&0x7f800000u)!=0x7f800000u;};
  if(!finite(angle)||!finite(left)||!finite(right)||std::abs(angle)>1.4f||std::abs(left)>1.5f||std::abs(right)>1.5f||left==right||encoded<2||encoded>=source||source>4096)return 2;
  const auto center=angles_to_center(angle,left,right);
  if(std::abs(center)>=.95f)return 2;
  std::vector<uint16_t> p;fill_param_2d(center,encoded,source,p);
  uint64_t count=0,pixels=0;for(size_t i=0;i<p.size();++i){count+=p[i];pixels+=p[i]*(std::abs(int(i)-int(p.size()/2))+1);}
  if(p.empty()||!(p.size()&1)||p.size()>129||count!=encoded||pixels!=source)return 3;
  std::cout<<"[";for(size_t i=0;i<p.size();++i)std::cout<<(i?",":"")<<p[i];std::cout<<"]\n";
 }
 return std::cin.eof()?0:2;
}
'''
    return (pre+first+second+main).encode()

def coverage(q,px,py,w,h,fov):
    sx=np.repeat(np.abs(np.arange(len(px))-len(px)//2)+1,px)
    sy=np.repeat(np.abs(np.arange(len(py))-len(py)//2)+1,py)
    need(int(sx.sum())==w and int(sy.sum())==h,'constructor span conservation')
    l,r,d,u=map(math.tan,fov)
    x=l+(r-l)*np.r_[0,np.cumsum(sx)]/w;y=u+(d-u)*np.r_[0,np.cumsum(sy)]/h
    mask=q.protected_mask(len(sx),len(sy),x,y)
    bad=mask&((sx[None,:]>1)|(sy[:,None]>1))
    mx,my=len(px)//2,len(py)//2
    ox=sum(n*(abs(i-mx)+1) for i,n in enumerate(px[:mx]));oy=sum(n*(abs(i-my)+1) for i,n in enumerate(py[:my]))
    return dict(encoded=[len(sx),len(sy)],ratio1_run=[px[mx],py[my]],ratio1_source_rect=[ox,oy,px[mx],py[my]],protected_encoded_cells=int(mask.sum()),protected_cells_with_span_above1=int(bad.sum()),protected_mask_sha256=sha(mask.tobytes()))

def run(run_root,manifest_sha,out):
    root=run_root.resolve();manifest=(root/'hashes.json').read_bytes();need(sha(manifest)==manifest_sha,'manifest pin')
    hashes=json.loads(manifest);inputs={};captures=[]
    need(type(hashes) is dict and 0<len(hashes)<=4096,'manifest population')
    for name,digest in hashes.items():
        need(type(name) is str and not Path(name).is_absolute() and all(v not in ('','.','..') for v in name.split('/')),'unsafe manifest path')
        need(type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest),'manifest digest')
    def read(name):
        p=root/name;cursor=root
        for part in Path(name).parts:cursor/=part;need(not cursor.is_symlink(),'symlink capture path')
        need(p.is_file() and p.stat().st_size<=4*1024*1024,'capture path/size')
        raw=p.read_bytes();need(sha(raw)==hashes[name],'capture hash '+name);inputs[name]=sha(raw);return raw
    for name in sorted(hashes):
        if re.fullmatch(r'client/codec-test/quality/capture-[1-3]-eye-[01]\.json',name):
            d=json.loads(read(name));need(name.endswith(f"capture-{d['capture_id']}-eye-{d['eye']}.json"),'capture filename identity');need(d['encoded_input_extent']==[960,1056] and [d['width'],d['height']]==[1800,1980],'observed geometry')
            need(all(type(d[k]) is int for k in ('capture_id','eye','frame','warp')) and d['capture_id'] in (1,2,3) and 0<d['frame']<2**63 and 0<=d['warp']<2**64,'capture typed identity')
            need(d['eye'] in (0,1) and d['projection_submitted'] is True and d['readback_fence_observed'] is True,'capture identity/completion')
            captures.append((name,d))
    need(len(captures)==6 and {(d['capture_id'],d['eye'])for _,d in captures}=={(i,e)for i in (1,2,3)for e in (0,1)},'exact three stereo capture population')
    for i in (1,2,3):need(len({(d['frame'],d['warp'])for _,d in captures if d['capture_id']==i})==1,'stereo frame/warp mismatch')
    provenance=json.loads(read('provenance.json'))
    out.mkdir(parents=True,exist_ok=False)
    source=SOURCE.read_bytes();quality_raw=QUALITY.read_bytes();script=Path(__file__).read_bytes()
    need(provenance['source_helper_sha256']['server/compositor/foveation.cpp']==sha(source),'captured native foveation source mismatch')
    generated=extract(source);cpp=out/'constructor.cpp';cpp.write_bytes(generated)
    compilers={x:shutil.which(name) for x,name in [('native','c++'),('sanitized','clang++')]};need(all(compilers.values()),'C++ compilers required')
    commands=[]
    def cmd(argv,**kw):
        start=time.monotonic();r=subprocess.run(argv,capture_output=True,timeout=30,**kw)
        commands.append(dict(argv=list(map(str,argv)),exit=r.returncode,wall_seconds=time.monotonic()-start,stdout_sha256=sha(r.stdout),stderr_sha256=sha(r.stderr)))
        need(r.returncode==0 and not r.stderr,'command failed '+repr(argv)+r.stderr.decode(errors='replace'));return r.stdout
    binaries={}
    for label,flags in [('native',['-O2','-ffast-math']),('sanitized',['-O1','-fno-omit-frame-pointer','-fsanitize=address,undefined'])]:
        binary=out/label;cmd([compilers[label],'-std=c++23',*flags,str(cpp),'-o',str(binary)]);binaries[label]=dict(path=str(binary),sha256=sha(binary.read_bytes()))
    specs=[];jobs=[]
    def add(angle,left,right,enc,src):
        specs.append(f'{angle:.17g} {left:.17g} {right:.17g} {enc} {src}\n');return len(specs)-1
    for name,d in captures:
        l,r,dn,up=d['encoded_input_fov_left_right_down_up_radians'];eye=d['eye']
        for ipd_half,pitch in [(0.033,10),(0.031,9),(0.031,10),(0.031,11),(0.033,9),(0.033,11),(0.035,9),(0.035,10),(0.035,11)]:
            angle=math.atan2((1 if eye==0 else -1)*ipd_half,1)
            for enc in [(960,1056),(1152,1280)]:
                x=add(angle,l,r,enc[0],d['width']);y=add(-pitch*math.pi/180,up,dn,enc[1],d['height'])
                jobs.append(dict(capture=name,eye=eye,half_ipd_m=ipd_half,downward_pitch_degrees=pitch,encoded=enc,x=x,y=y))
    payload=''.join(specs).encode();raw=cmd([binaries['native']['path']],input=payload)
    rows=[json.loads(x)for x in raw.splitlines()];need(len(rows)==len(specs),'constructor output population')
    sanitized=cmd([binaries['sanitized']['path']],input=payload)
    san_rows=[json.loads(x)for x in sanitized.splitlines()];need(len(san_rows)==len(rows),'sanitizer population')
    # Floating optimization can affect integer partition boundaries; preserve any difference rather than silently substituting.
    sanitizer_partition_differences=sum(a!=b for a,b in zip(rows,san_rows))
    for label in binaries:
        for bad in [b'0 -1 1 0 1800\n',b'0 -1 1 1800 1800\n',b'0 -1 1 1152 5000\n',b'0 0 0 960 1800\n',b'1.5 -1 1 960 1800\n']:
            r=subprocess.run([binaries[label]['path']],input=bad,capture_output=True,timeout=5);need(r.returncode==2 and not r.stderr and not r.stdout,'negative constructor admission')
    q=load_quality();byname=dict(captures);results=[]
    for j in jobs:
        d=byname[j['capture']];px,py=rows[j.pop('x')],rows[j.pop('y')]
        nominal=j['half_ipd_m']==.033 and j['downward_pitch_degrees']==10
        if nominal and j['encoded']==(960,1056):need(px==d['foveation_x'] and py==d['foveation_y'],'nominal model fails observed mapping')
        c=coverage(q,px,py,d['width'],d['height'],d['encoded_input_fov_left_right_down_up_radians'])
        results.append(j|c|dict(x_params=px,y_params=py,nominal=nominal))
    for name,digest in inputs.items():need(sha((root/name).read_bytes())==digest,'capture changed')
    for binary in binaries.values():need(sha(Path(binary['path']).read_bytes())==binary['sha256'],'binary changed')
    need((root/'hashes.json').read_bytes()==manifest and SOURCE.read_bytes()==source and QUALITY.read_bytes()==quality_raw and Path(__file__).read_bytes()==script,'source changed')
    result=dict(schema=1,status='pass',scope='Conditional offline allocation prediction; no new headset, GPU, wire admission or quality qualification',run_root=str(root),run_manifest_sha256=manifest_sha,input_sha256=inputs,
      source_sha256={str(SOURCE.relative_to(ROOT)):sha(source),str(QUALITY.relative_to(ROOT)):sha(quality_raw),str(Path(__file__).relative_to(ROOT)):sha(script)},generated_cpp_sha256=sha(generated),compilers={k:dict(invocation=v,resolved=str(Path(v).resolve()),sha256=sha(Path(v).resolve().read_bytes())) for k,v in compilers.items()},binaries=binaries,commands=commands,
      constructor_invocations=len(specs)*2,negative_admissions=10,sanitizer_partition_differences=sanitizer_partition_differences,results=results,
      assumptions=['Source rect is full1800x1980, canonical axes; actual source mapping capture still required.','Zero gaze quaternion, no manual override,1m convergence, nominal eye_x±0.033m and10degree downward default; these are declared inputs, not uniquely recovered tracking measurements.','Nominal constructor matches all six captured960x1056 vectors exactly; matching does not prove inputs unique.','Nine finite eye-offset/pitch combinations are sensitivity cases, not exhaustive or continuous bounds.','Source-code -O2 -ffast-math standalone extraction is not final linked binary execution.','Protected mask covers encoded cell footprints, not original-app or displayed byte equality.'],
      stereo_envelope_cap=262144,wire_admission_measured=False,actual_1152_execution=False,quality_route_qualified=False,full_goal_qualified=False)
    (out/'result.json').write_text(json.dumps(result,indent=2)+'\n');return result

if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args();r=run(a.run,a.manifest_sha256,a.out);print(json.dumps(dict(status=r['status'],cases=len(r['results']),constructor_invocations=r['constructor_invocations'],sanitizer_partition_differences=r['sanitizer_partition_differences'])))
