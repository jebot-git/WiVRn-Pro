#!/usr/bin/env python3
"""Prepare/audit a bounded replay using the unchanged ADB runner; no device calls."""
import argparse,base64,hashlib,json,re,shlex,struct,subprocess,tempfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[3]
QUALIFIED='508c290be9c3a02d28a5162fbad1a1d8674ebfe1'
RUNNER_SHA='e495081a5bdbbef3aa94cff0082b700516740dca711d10d3ec9dedb143e4c64a'
EXP='prototypes/vr444/experiments/submitted_image_replay/'
MAX_IMAGE=16*1024*1024

def need(value,message):
    if not value:raise ValueError(message)
def digest(raw):return hashlib.sha256(raw).hexdigest()
def strict(raw):
    def pairs(values):
        result={}
        for key,value in values:need(key not in result,'duplicate JSON key');result[key]=value
        return result
    def constant(value):raise ValueError('nonfinite JSON '+value)
    return json.loads(raw,object_pairs_hook=pairs,parse_constant=constant)
def read(path,limit,expected=None,allow_empty=False):
    path=Path(path);need(path.is_file() and not path.is_symlink(),'regular input required')
    with path.open('rb') as stream:raw=stream.read(limit+1)
    need((allow_empty or len(raw)>0)and len(raw)<=limit,'input byte bound')
    need(expected is None or digest(raw)==expected,'input digest mismatch: '+str(path))
    return raw
def u32(value,low,high):need(type(value)is int and low<=value<=high,'integer bound');return value
def pinned(path,sha,limit):
    need(type(sha)is str and re.fullmatch('[0-9a-f]{64}',sha),'explicit digest required')
    return read(path,limit,sha)
def historical():
    receipt=strict(subprocess.check_output(['git','show',QUALIFIED+':'+EXP+'results/qualification.json'],cwd=ROOT))
    need(receipt['status']=='pass' and receipt['quality_qualified'] is False,'qualified executable scope')
    for name,sha in receipt['source_sha256'].items():need(digest(subprocess.check_output(['git','show',QUALIFIED+':'+name],cwd=ROOT))==sha,'historical source binding')
    return receipt

def renderer(receipt_path,expected_sha):
    raw=pinned(receipt_path,expected_sha,16*1024*1024);receipt=strict(raw);root=Path(receipt['source'])
    need(root.is_absolute(),'native source root');bindings={str(Path(receipt_path).resolve()):digest(raw)};shaders={}
    def item(name,limit):
        need(type(name)is str and not Path(name).is_absolute() and '..'not in Path(name).parts,'native relative path')
        data=read(root/name,limit,receipt['sha256'][name]);bindings[str(root/name)]=digest(data);return data
    item('client/shaders/reprojection.glsl',1024*1024);item('cmake/CompileGLSL.cmake',1024*1024)
    cache_names=[name for name in receipt['sha256'] if name.endswith('/arm64-v8a/CMakeCache.txt')];need(len(cache_names)==1,'native Android cache population')
    cache=item(cache_names[0],4*1024*1024).decode();opt=re.findall(r'^WIVRN_OPTIMIZE_SHADERS:BOOL=(ON|OFF)$',cache,re.M);need(len(opt)==1,'shader optimizer selection')
    need(set(receipt['renderer_shaders'])=={'reprojection.vert','reprojection.frag'},'native shader population')
    for name,entry in receipt['renderer_shaders'].items():
        need(entry['literal_equivalent'] is True and entry['path'].endswith('.spv-'+('opt'if opt[0]=='ON'else'nopt')),'native selected shader')
        code=item(entry['path'],1024*1024);literal=item(entry['embedded_path'],4*1024*1024)
        need(digest(code)==entry['sha256'] and digest(literal)==entry['embedded_sha256'],'native shader map join')
        need(len(code)%4==0 and code[:4]==b'\x03\x02\x23\x07','binary SPIR-V')
        text=literal.decode('ascii');tokens=re.findall(r'0x[0-9a-fA-F]{1,8}\s*,',text)
        need(re.sub(r'0x[0-9a-fA-F]{1,8}\s*,','',text).strip()=='','unexpected embedded shader text')
        decoded=b''.join(struct.pack('<I',int(token.rstrip(',').strip(),16))for token in tokens)
        need(decoded==code,'embedded shader bytes differ from selected binary');shaders[name]=code
    return shaders,bindings

def adapter_contract(adapter,shaders,native_sha,serial):
    need(adapter['build_receipt_sha256']==native_sha,'adapter/native build splice')
    need(adapter['candidate_capture_audit']['headset_serial_recorded']==serial,'native/replay headset identity splice')
    need(set(adapter['renderer_shaders'])==set(shaders),'adapter shader population')
    for name,code in shaders.items():
        item=adapter['renderer_shaders'][name]
        need(item['sha256']==digest(code) and item['literal_equivalence_recomputed'] is True
             and type(item['actual_bytes'])is int and item['actual_bytes']==len(code),'adapter shader byte join')

def expected_runs(device=0):
    return [
        dict(name='replay-guards',argv=['{remote}/replay','{remote}/job.rlp','--cpu-only'],timeout_s=10),
        dict(name='replay-render',argv=['{remote}/replay','{remote}/job.rlp','{remote}/reprojection.vert.spv','{remote}/reprojection.frag.spv','{remote}/render',str(device)],timeout_s=60),
        dict(name='left-hash',argv=['sha256sum','{remote}/render-eye-0.raw'],timeout_s=30),
        dict(name='left-bytes',argv=['base64','{remote}/render-eye-0.raw'],timeout_s=30),
        dict(name='right-hash',argv=['sha256sum','{remote}/render-eye-1.raw'],timeout_s=30),
        dict(name='right-bytes',argv=['base64','{remote}/render-eye-1.raw'],timeout_s=30),
        dict(name='replay-omission',argv=['{remote}/replay','{remote}/job.rlp','{remote}/reprojection.vert.spv','{remote}/reprojection.frag.spv','{remote}/omitted',str(device),'--omit-right'],timeout_s=60),
        dict(name='omission-left-hash',argv=['sha256sum','{remote}/omitted-eye-0.raw'],timeout_s=30),
        dict(name='omission-right-hash',argv=['sha256sum','{remote}/omitted-eye-1.raw'],timeout_s=30),
        dict(name='omission-right-bytes',argv=['base64','{remote}/omitted-eye-1.raw'],timeout_s=30)]

def prepare(job_receipt,job_receipt_sha,native_receipt,native_sha,bundle,adb_target,serial,device=0):
    u32(device,0,15);need(type(adb_target)is str and re.fullmatch('[A-Za-z0-9_.:-]{1,120}',adb_target),'ADB target');need(type(serial)is str and re.fullmatch('[A-Za-z0-9_-]{1,120}',serial),'device serial')
    adapter_raw=pinned(job_receipt,job_receipt_sha,16*1024*1024);adapter=strict(adapter_raw)
    need(adapter['status']=='pass','job adapter did not pass');desc=adapter['job'];need(desc['format']=='RLP1' and type(desc['version'])is int and desc['version']==1,'job format/version');path=Path(desc['path']);path=path if path.is_absolute()else Path(job_receipt).parent/path
    job=read(path,40*1024*1024,desc['sha256']);need(type(desc['bytes'])is int and len(job)==desc['bytes'],'job byte count')
    need(len(job)>=24,'job header');magic,version,w,h,fmt,eyes=struct.unpack('<6I',job[:24]);u32(w,1,4096);u32(h,1,4096);need(w*h<=4194304 and(magic,version,eyes)==(0x31504c52,1,2)and fmt in(43,50),'job header scope')
    need(all(type(adapter['output'].get(k))is int for k in('width','height','vk_format'))and adapter['output']==dict(width=w,height=h,vk_format=fmt),'job output adapter join')
    qualified=historical();binary={suffix:next((name,sha)for name,sha in qualified['binary_sha256'].items()if Path(name).name==suffix)for suffix in('replay','replay-android')}
    host=read(binary['replay'][0],64*1024*1024,binary['replay'][1]);android=read(binary['replay-android'][0],64*1024*1024,binary['replay-android'][1]);need(host[:4]==android[:4]==b'\x7fELF','qualified ELF bytes')
    shaders,bindings=renderer(native_receipt,native_sha);adapter_contract(adapter,shaders,native_sha,serial);bindings[str(Path(job_receipt).resolve())]=digest(adapter_raw);bindings[str(path.resolve())]=digest(job)
    with tempfile.TemporaryDirectory(prefix='sub15-replay-admission-')as temp:
        temp=Path(temp);exe=temp/'host';exe.write_bytes(host);exe.chmod(0o500);inp=temp/'job';inp.write_bytes(job)
        run=subprocess.run([str(exe),str(inp),'--cpu-only'],capture_output=True,timeout=10)
        need(run.returncode==0 and not run.stderr and strict(run.stdout)==dict(job_valid=True,eyes=2,gpu_used=False),'qualified native job grammar admission')
    bundle=Path(bundle);need(bundle.is_absolute()and not bundle.exists(),'fresh absolute bundle required');bundle.mkdir()
    files=[]
    for name,data,executable in [('replay',android,True),('reprojection.vert.spv',shaders['reprojection.vert'],False),('reprojection.frag.spv',shaders['reprojection.frag'],False),('job.rlp',job,False)]:
        out=bundle/name;out.write_bytes(data);out.chmod(0o500 if executable else 0o400)
        files.append(dict(local=str(out),remote=name,sha256=digest(data),**({'executable':True}if executable else{})))
    for name,sha in bindings.items():read(name,40*1024*1024,sha)
    plan=dict(schema=1,adb_target=adb_target,expected_serial=serial,files=files,runs=expected_runs(device),replay=dict(qualified_commit=QUALIFIED,device_index=device,output=dict(width=w,height=h,vk_format=fmt),job_sha256=digest(job),source_adapter_sha256=digest(adapter_raw),native_build_receipt_sha256=native_sha,control_sha256=bindings,host_parser_sha256=binary['replay'][1],planner_sha256=digest(Path(__file__).read_bytes()),quality_qualified=False,scope='Pinned private graphics replay acquisition; source adapter and native capture comparison remain independent gates'))
    (bundle/'plan.json').write_text(json.dumps(plan,indent=2)+'\n');return plan

def audit(directory,plan_path,runner,output):
    directory,plan_path,runner,output=map(Path,(directory,plan_path,runner,output));need(not output.exists(),'fresh audit output directory')
    plan_raw=read(plan_path,1024*1024);plan=strict(plan_raw);need(digest(read(runner,1024*1024))==RUNNER_SHA,'fixed acquisition runner')
    rp=plan['replay'];need(rp['qualified_commit']==QUALIFIED and rp['quality_qualified'] is False,'plan scope');u32(rp['device_index'],0,15)
    need(plan['runs']==expected_runs(rp['device_index']),'run population or arguments')
    shape=rp['output'];w=u32(shape['width'],1,4096);h=u32(shape['height'],1,4096);fmt=shape['vk_format'];need(w*h<=4194304 and type(fmt)is int and fmt in(43,50),'output shape/format');size=w*h*4
    need([f['remote']for f in plan['files']]==['replay','reprojection.vert.spv','reprojection.frag.spv','job.rlp'],'upload population')
    qualified=historical();expected_binary=next(sha for name,sha in qualified['binary_sha256'].items()if Path(name).name=='replay-android')
    need(plan['files'][0]['sha256']==expected_binary and plan['files'][0].get('executable')is True and all('executable'not in f for f in plan['files'][1:]),'qualified executable population')
    files=list(directory.iterdir());need(len(files)==53,'bounded fixed acquisition file population')
    def limit(name):return MAX_IMAGE*2 if name in('left-bytes.stdout','right-bytes.stdout','omission-right-bytes.stdout')else 1024*1024
    snapshots={p.name:read(p,limit(p.name),allow_empty=True)for p in files}
    inventory={name:digest(raw)for name,raw in snapshots.items()};receipt=strict(snapshots['receipt.json']);remote=receipt['remote_directory'];need(re.fullmatch('/data/local/tmp/sub15-review-[0-9a-f]{32}',remote),'owned remote path')
    need(receipt['status']=='completed'and receipt['inputs_unchanged']is True and receipt['headset_used']is True and receipt['openxr_used']is False and receipt['performance_qualification']is False and receipt['promotion_eligible']is False,'acquisition completion/scope')
    need(receipt['plan_sha256']==digest(plan_raw)and receipt['runner_sha256']==RUNNER_SHA and receipt['inputs']==plan['files'],'acquisition binding')
    adb=['adb','-s',plan['adb_target']]
    def shell(argv):return adb+['shell',shlex.join(argv)]
    commands={name:(shell(argv),30)for name,argv in [('serial',['getprop','ro.serialno']),('fingerprint',['getprop','ro.build.fingerprint']),('battery-before',['dumpsys','battery']),('mkdir',['mkdir',remote])]}
    for i,f in enumerate(plan['files']):
        destination=remote+'/'+f['remote'];commands['push-'+str(i)]=(adb+['push',f['local'],destination],60);commands['hash-'+str(i)]=(shell(['sha256sum',destination]),30)
        if f.get('executable'):commands['chmod-'+str(i)]=(shell(['chmod','700',destination]),30)
        need(snapshots['hash-'+str(i)+'.stdout'].decode().split()==[f['sha256'],destination],'deployed input hash/path')
        read(f['local'],64*1024*1024,f['sha256'])
    for r in plan['runs']:commands[r['name']]=(shell(['timeout','-s','KILL',str(r['timeout_s'])]+[v.replace('{remote}',remote)for v in r['argv']]),r['timeout_s']+15)
    for name,argv in [('battery-after',['dumpsys','battery']),('cleanup',['rm','-rf',remote]),('cleanup-verified',['test','!','-e',remote])]:commands[name]=(shell(argv),30)
    need([c['label']for c in receipt['commands']]==list(commands),'command order/population')
    need(set(snapshots)=={'receipt.json'}|{name+'.'+stream for name in commands for stream in('stdout','stderr')},'capture population')
    for c in receipt['commands']:
        argv,timeout=commands[c['label']];need(c['argv']==argv and type(c['timeout_s'])is int and c['timeout_s']==timeout,'command argv/timeout')
        need(type(c['returncode'])is int and c['returncode']==0 and c.get('host_timeout',False)is False,'command failure/timeout')
        for stream in('stdout','stderr'):need(c[stream+'_sha256']==inventory[c['label']+'.'+stream],'raw command hash')
    need(receipt['device_serial']==plan['expected_serial']==snapshots['serial.stdout'].decode().strip(),'device serial')
    need(snapshots['cleanup-verified.stdout']==snapshots['cleanup-verified.stderr']==b'','cleanup verification')
    for name in [r['name']for r in plan['runs']]:need(snapshots[name+'.stderr']==b'','replay diagnostic stderr')
    guards=strict(snapshots['replay-guards.stdout']);need(guards==dict(job_valid=True,eyes=2,gpu_used=False)and guards.get('job_valid')is True and guards.get('gpu_used')is False and type(guards.get('eyes'))is int,'device parser result')
    identities=[]
    for label,omission in [('replay-render',False),('replay-omission',True)]:
        row=strict(snapshots[label+'.stdout']);need(row['replay_completed']is True and row['omission_control']is omission and row['openxr_used']is False and row['quality_qualified']is False and row['performance_claim']is False,'renderer scope')
        need(all(type(row[k])is int for k in('eyes','width','height','vk_format','device_type','vendor_id','device_id','driver','api_version','queue_family')),'renderer integer types')
        need((row['eyes'],row['width'],row['height'],row['vk_format'])==(2,w,h,fmt)and row['device_type']in(1,2),'renderer geometry/hardware')
        need(type(row['device'])is str and row['device'],'device string');identities.append({k:row[k]for k in('device','device_type','vendor_id','device_id','driver','api_version','queue_family')})
    need(identities[0]==identities[1],'device identity changed')
    def hashline(label,path):
        value=snapshots[label+'.stdout'].decode().split();need(len(value)==2 and re.fullmatch('[0-9a-f]{64}',value[0])and value[1]==remote+'/'+path,'output hash/path');return value[0]
    def decoded(label):
        raw=snapshots[label+'.stdout'];need(len(raw)<=((size+2)//3)*4+((size+2)//3)*4//60+1024,'encoded output bound')
        compact=re.sub(rb'[\r\n]',b'',raw);need(len(compact)==((size+2)//3)*4,'base64 population');data=base64.b64decode(compact,validate=True);need(len(data)==size,'decoded output size');return data
    eyes=[decoded('left-bytes'),decoded('right-bytes')];omitted=decoded('omission-right-bytes')
    for eye,label in enumerate(('left','right')):need(digest(eyes[eye])==hashline(label+'-hash',f'render-eye-{eye}.raw'),'output bytes/hash')
    need(hashline('omission-left-hash','omitted-eye-0.raw')==digest(eyes[0]),'omission changed left image')
    need(digest(omitted)==hashline('omission-right-hash','omitted-eye-1.raw')and omitted==bytes([0x5a])*size and omitted!=eyes[1],'right omission control did not distinguish image')
    for name,sha in rp['control_sha256'].items():read(name,40*1024*1024,sha)
    def control_path(sha):
        paths=[name for name,value in rp['control_sha256'].items()if value==sha];need(len(paths)==1,'unique control receipt binding');return Path(paths[0])
    shader_receipt=control_path(rp['native_build_receipt_sha256']);shaders,bindings=renderer(shader_receipt,rp['native_build_receipt_sha256'])
    need(all(rp['control_sha256'].get(name)==sha for name,sha in bindings.items()),'native control population')
    need([plan['files'][i]['sha256']for i in(1,2)]==[digest(shaders[name])for name in('reprojection.vert','reprojection.frag')],'deployed native shader join')
    adapter_path=control_path(rp['source_adapter_sha256']);adapter=strict(read(adapter_path,16*1024*1024,rp['source_adapter_sha256']))
    need(adapter['status']=='pass'and adapter['job']['format']=='RLP1'and type(adapter['job']['version'])is int and adapter['job']['version']==1 and all(type(adapter['output'].get(k))is int for k in('width','height','vk_format'))and adapter['output']==shape,'source adapter result join')
    adapter_contract(adapter,shaders,rp['native_build_receipt_sha256'],plan['expected_serial'])
    job_path=Path(adapter['job']['path']);job_path=job_path if job_path.is_absolute()else adapter_path.parent/job_path
    job=read(job_path,40*1024*1024,adapter['job']['sha256']);need(type(adapter['job']['bytes'])is int and len(job)==adapter['job']['bytes'] and rp['job_sha256']==plan['files'][3]['sha256']==digest(job),'job input join')
    need(set(rp['control_sha256'])==set(bindings)|{str(adapter_path),str(job_path.resolve())},'complete control binding population')
    need(rp['host_parser_sha256']==next(sha for name,sha in qualified['binary_sha256'].items()if Path(name).name=='replay'),'qualified host parser binding')
    need(struct.unpack('<6I',job[:24])==(0x31504c52,1,w,h,fmt,2),'audited job header')
    need(rp['planner_sha256']==digest(Path(__file__).read_bytes()),'planner/auditor source mismatch')
    need(inventory=={p.name:digest(read(p,limit(p.name),allow_empty=True))for p in directory.iterdir()},'capture changed')
    need(read(plan_path,1024*1024)==plan_raw and digest(read(runner,1024*1024))==RUNNER_SHA,'control changed')
    output.mkdir();outfiles=[]
    for eye,data in enumerate(eyes):
        raw=output/f'eye-{eye}.raw';raw.write_bytes(data);outfiles.append(dict(path=str(raw),sha256=digest(data),bytes=len(data),channel_order='RGBA'if fmt==43 else'BGRA'))
    result=dict(schema=1,status='pass',actual_headset_used=True,device=identities[0],qualified_commit=QUALIFIED,plan_sha256=digest(plan_raw),capture_sha256=inventory,output=shape,eyes=outfiles,omission_control_verified=True,command_count=len(commands),raw_format_preserved=True,source_adapter_sha256=rp['source_adapter_sha256'],native_build_receipt_sha256=rp['native_build_receipt_sha256'],quality_qualified=False,native_capture_comparison_performed=False,scope='Bound standalone actual-device rendering and exact retained bytes; source/capture-quality comparison is a separate gate',auditor_sha256=digest(Path(__file__).read_bytes()))
    (output/'audit.json').write_text(json.dumps(result,indent=2)+'\n');return result

def main():
    p=argparse.ArgumentParser(description=__doc__);s=p.add_subparsers(dest='mode',required=True)
    make=s.add_parser('prepare');make.add_argument('--job-receipt',type=Path,required=True);make.add_argument('--job-receipt-sha256',required=True);make.add_argument('--native-receipt',type=Path,required=True);make.add_argument('--native-receipt-sha256',required=True);make.add_argument('--bundle',type=Path,required=True);make.add_argument('--adb-target',required=True);make.add_argument('--serial',required=True);make.add_argument('--device',type=int,default=0)
    check=s.add_parser('audit');check.add_argument('directory',type=Path);check.add_argument('--plan',type=Path,required=True);check.add_argument('--runner',type=Path,required=True);check.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if a.mode=='prepare':result=prepare(a.job_receipt,a.job_receipt_sha256,a.native_receipt,a.native_receipt_sha256,a.bundle,a.adb_target,a.serial,a.device);print(str(a.bundle/'plan.json'))
    else:result=audit(a.directory,a.plan,a.runner,a.output);print(json.dumps({k:result[k]for k in('status','actual_headset_used','command_count','quality_qualified')}))
if __name__=='__main__':main()
