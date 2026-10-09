#!/usr/bin/env python3
"""Synthetic private renderer qualification, never native-capture quality evidence."""
import argparse,hashlib,json,math,os,struct,subprocess,tempfile,shutil
from pathlib import Path
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def axis(a):return sum(n*(abs(len(a)//2-i)+1) for i,n in enumerate(a))
def rgba(w,h,eye):return bytes(v for y in range(h) for x in range(w) for v in ((x*37+y*17+eye*53)%256,(x*3+y*251+eye*139)%256,(x*131+y*13+eye*97)%256,255))
def job(x,y,fmt=43,scale=1.,bias=0.):
    w,h=sum(x),sum(y);out=bytearray(struct.pack('<6I',0x31504c52,1,axis(x),axis(y),fmt,2));eyes=[]
    for eye in range(2):
        pixels=rgba(w,h,eye);eyes.append(pixels)
        out+=struct.pack('<6I',w,h,len(x),len(y),0,0)
        out+=struct.pack('<8i20f',0,0,w,h,0,0,0,0,*([scale]*3+[1]),*([bias]*3+[0]),*([0]*12))
        out+=struct.pack('<'+'I'*(len(x)+len(y)),*(x+y));out+=pixels
    return bytes(out),eyes
def linear(code):
    x=code/255
    return x/12.92 if x<=.04045 else ((x+.055)/1.055)**2.4
def encoded(v):return round((12.92*v if v<=.0031308 else 1.055*v**(1/2.4)-.055)*255)
def coordinate(a,p):
    base=source=0
    for i,n in enumerate(a):
        ratio=abs(len(a)//2-i)+1
        if p+.5<base+n*ratio:return source+(p+.5-base)/ratio-.5
        base+=n*ratio;source+=n
    raise ValueError('coordinate')
def ideal(source,x,y,fmt,scale,bias):
    w,h=sum(x),sum(y);out=[]
    for oy in range(axis(y)):
        sy=coordinate(y,oy);ya=math.floor(sy);fy=sy-ya
        for ox in range(axis(x)):
            sx=coordinate(x,ox);xa=math.floor(sx);fx=sx-xa;pixel=[]
            for c in range(3):
                v=0
                for dy in (0,1):
                    for dx in (0,1):
                        p=(min(max(ya+dy,0),h-1)*w+min(max(xa+dx,0),w-1))*4+c
                        v+=linear(source[p])*(fy if dy else 1-fy)*(fx if dx else 1-fx)
                pixel.append(encoded(min(max(v*scale+bias,0),1)))
            if fmt==50:pixel.reverse()
            out.extend(pixel+[255])
    return bytes(out)
COMMANDS=[]
def run(argv,timeout=20):
    value=subprocess.run(['timeout','-s','KILL',str(timeout),*map(str,argv)],capture_output=True,timeout=timeout+5)
    COMMANDS.append(dict(argv=list(map(str,argv)),external_timeout_s=timeout,returncode=value.returncode,stdout=value.stdout.decode(),stderr=value.stderr.decode()))
    return value

def main():
    p=argparse.ArgumentParser();p.add_argument('--build',type=Path,required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--device',type=int,default=0);p.add_argument('--cpu-only',action='store_true');a=p.parse_args()
    sources={str(f.relative_to(ROOT)):sha(f) for f in HERE.iterdir() if f.is_file()}
    assert a.build.is_absolute() and not a.build.exists(), 'fresh absolute build directory required'
    build=run(['bash',HERE/'build.sh',a.build],120);assert build.returncode==0 and not build.stderr,(build.returncode,build.stderr)
    binaries={str(f):sha(f) for f in [a.build/'replay',a.build/'replay-asan',a.build/'replay-android',*sorted((a.build/'shaders').glob('*.spv'))]};checks=[]
    with tempfile.TemporaryDirectory(prefix='sub15-render-qualification-') as tmp:
        tmp=Path(tmp);valid,_=job([17],[33]);path=tmp/'job.rlp';path.write_bytes(valid)
        result=run([a.build/'replay-asan',path,'--cpu-only']);assert result.returncode==0 and not result.stderr
        mutations=[valid[:n] for n in (1,23,40,len(valid)-1)]+[valid+b'X']
        for offset,value in [(0,0),(4,2),(8,0),(12,4097),(16,37),(20,1),(24,0),(32,130),(40,1),(44,1),(48,1)]:
            changed=bytearray(valid);struct.pack_into('<I',changed,offset,value);mutations.append(bytes(changed))
        changed=bytearray(valid);struct.pack_into('<I',changed,80,0x7fc00000);mutations.append(bytes(changed))
        changed=bytearray(valid);changed[171]=0;mutations.append(bytes(changed)) # first source alpha byte
        for changed in mutations:
            path.write_bytes(changed);r=run([a.build/'replay-asan',path,'--cpu-only']);assert r.returncode==1 and b'submitted-image-replay:' in r.stderr and b'Sanitizer' not in r.stderr
        checks.append(dict(name='bounded job ASan/UBSan',valid=1,rejections=len(mutations)))
        if not a.cpu_only:
            identity=None
            for case,(x,y,fmt,scale,bias) in enumerate([([256],[2],43,1.,0.),([17],[33],50,1.,0.),([1,4,1],[1,2,1],43,1.,0.),([9],[5],43,.5,.1)]):
                raw,eyes=job(x,y,fmt,scale,bias);path.write_bytes(raw);outputs=[]
                for iteration in range(2):
                    prefix=tmp/f'case{case}-{iteration}'
                    r=run([a.build/'replay',path,a.build/'shaders/reprojection.vert.spv',a.build/'shaders/reprojection.frag.spv',prefix,a.device]);assert r.returncode==0 and not r.stderr,(r.returncode,r.stderr)
                    observation=json.loads(r.stdout);current={k:observation[k] for k in ('device','device_type','vendor_id','device_id','driver','api_version','queue_family')};assert observation['replay_completed'] is True and observation['quality_qualified'] is False and observation['openxr_used'] is False
                    assert identity is None or identity==current;identity=current
                    output=[Path(str(prefix)+f'-eye-{eye}.raw').read_bytes() for eye in range(2)];assert output[0]!=output[1],'eye identity collapsed';outputs.append(output)
                assert outputs[0]==outputs[1],'repeated render byte instability'
                errors=[]
                for eye in range(2):
                    expected=ideal(eyes[eye],x,y,fmt,scale,bias);actual=outputs[0][eye];assert len(actual)==len(expected)
                    error=max(abs(x-y) for x,y in zip(actual,expected));assert error<=1,(case,eye,error)
                    assert all(actual[i]==255 for i in range(3,len(actual),4));errors.append(error)
                    if len(x)==len(y)==1 and scale==1 and bias==0:assert actual==expected,'identity render not byte exact'
                prefix=tmp/f'case{case}-omit';r=run([a.build/'replay',path,a.build/'shaders/reprojection.vert.spv',a.build/'shaders/reprojection.frag.spv',prefix,a.device,'--omit-right']);assert r.returncode==0 and not r.stderr
                omitted=[Path(str(prefix)+f'-eye-{eye}.raw').read_bytes() for eye in range(2)];assert omitted[0]==outputs[0][0] and omitted[1]!=outputs[0][1] and set(omitted[1])=={0x5a}
                checks.append(dict(name='synthetic graphics',case=case,format=fmt,source=[sum(x),sum(y)],output=[axis(x),axis(y)],repeat_exact=True,eye_identity=True,omission_control_rejected=True,ideal_cpu_max_code_error=errors,output_sha256=[hashlib.sha256(x).hexdigest() for x in outputs[0]]))
            # ASan executes the same GPU path once, beyond host-only parser tests.
            prefix=tmp/'asan';r=run([a.build/'replay-asan',path,a.build/'shaders/reprojection.vert.spv',a.build/'shaders/reprojection.frag.spv',prefix,a.device]);assert r.returncode==0 and not r.stderr,(r.returncode,r.stderr)
            checks.append(dict(name='GPU ASan/UBSan',pass_=True))
    assert sources=={str(f.relative_to(ROOT)):sha(f) for f in HERE.iterdir() if f.is_file()}
    assert binaries=={name:sha(Path(name)) for name in binaries}
    a.output.write_text(json.dumps(dict(status='pass',source_sha256=sources,binary_sha256=binaries,checks=checks,device=identity if not a.cpu_only else None,hardware_executed=not a.cpu_only,headset_used=False,native_renderer_executed=False,synthetic_shaders_used=True,packaged_native_shader_binding_verified=False,quality_qualified=False,commands=COMMANDS,tools_sha256={name:sha(Path(shutil.which(name))) for name in ('clang++','glslc')},scope='Synthetic private graphics replay identity and CPU ideal ±1-code approximation checks; native capture matching is still required'),indent=2)+'\n')
    print(json.dumps(dict(status='pass',checks=len(checks),hardware_executed=not a.cpu_only)))
if __name__=='__main__':main()
