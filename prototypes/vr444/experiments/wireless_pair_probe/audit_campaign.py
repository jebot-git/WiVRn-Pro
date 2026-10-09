#!/usr/bin/env python3
"""Independently re-audit the saved finite Quest campaign; opens no sockets."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess
import audit_network

PROBE_COMMIT='9ec0b0d1d7eaf3cdb54709acfa1d2bcec9d163fb'
EXPECTED_NAMES=['smoke-tcp','smoke-udp','low-tcp-1','low-udp-1','low-udp-2','low-tcp-2','high-tcp-1','high-udp-1','high-udp-2','high-tcp-2']
def need(b,message):
    if not b:raise ValueError(message)
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def inventory(directory):
    files=sorted(directory.iterdir());need(all(p.is_file() and not p.is_symlink() for p in files),'flat regular receipt inventory required')
    return {p.name:sha(p) for p in files}
def check_logs(receipt,directory):
    result={}
    for command in receipt['commands']:
        label=command['label'];need(isinstance(label,str) and re.fullmatch(r'[a-zA-Z0-9_-]+',label) and label not in result,'invalid/duplicate label')
        need(type(command['returncode']) is int and command['returncode']==0 and command.get('host_timeout',False) is False,'acquisition command failed or timed out')
        for suffix in ['stdout','stderr']:need(sha(directory/(label+'.'+suffix))==command[suffix+'_sha256'],'changed command log')
        result[label]=command
    return result

def check_plan(plan):
    arms=plan['optional_smoke']+plan['runs'];need([a['name'] for a in arms]==EXPECTED_NAMES,'changed arm population/order')
    need(plan['host_ipv4']=='192.168.1.106' and plan['headset_ipv4']=='192.168.1.105' and plan['expected_host_interface']=='enp113s0','unexpected physical path')
    need(plan['expected_headset_serial']=='230YC01D8W000E','physical headset')
    tokens={};ports=set()
    for a in arms:
        name=a['name'];frames,size,hz=(5,32768,20) if name.startswith('smoke') else (60,32768,20) if name.startswith('low') else (180,98304,90)
        need((a['frames'],a['pair_bytes'],a['hz'])==(frames,size,hz),'profile population')
        transport='tcp' if '-tcp' in name else 'udp';need(a['transport']==transport,'profile transport')
        port=a['receiver_port'];need(type(port) is int and 49152<=port<=65535 and port not in ports,'owned port collision');ports.add(port)
        send=a['sender_argv'];receive=a['receiver_argv'];need(len(send)==len(receive)==10,'endpoint argument count')
        token=send[6];need(re.fullmatch('[a-f0-9]{32}',token) and int(token,16),'token')
        need(send[1:]==['send',transport,plan['host_ipv4'],plan['headset_ipv4'],str(port),token,str(frames),str(size),str(hz)],'sender args')
        need(receive[1:]==['receive',transport,plan['headset_ipv4'],plan['host_ipv4'],str(port),token,str(frames),str(size),str(hz)],'receiver args')
        group=a['matched_token_group'];expected_group='smoke' if name.startswith('smoke') else name.split('-')[0]+'-'+name.split('-')[-1]
        need(group==expected_group and tokens.setdefault(group,token)==token,'unmatched TCP UDP input bytes')
        need(a['nominal_ipv4_udp_offered_mbps']==(size+((size+1159)//1160)*68+84)*8*hz/1e6,'nominal offered accounting')
        need(send[0]==plan['host_binary']['path'] and receive[0]=='{remote}/probe','binary argument path')
    need(ports==set(plan['require_free_receiver_ports']),'port check inventory')
    return arms

def qualified_artifacts(plan,qualified):
    prefix='/tmp/wivrn-wireless-pair-build/'
    need(plan['host_binary']['sha256']==qualified['binary_sha256'][prefix+'probe'],'host probe is not the pinned qualified binary')
    files=plan['files'];need(len(files)==2 and {f['remote'] for f in files}=={'probe','grammar-test'},'device artifact population')
    for file in files:
        name='probe-android' if file['remote']=='probe' else 'test-android'
        need(file['sha256']==qualified['binary_sha256'][prefix+name],'device artifact is not the pinned qualified binary')

def required_commands(plan,remote,directory,auditor):
    need(re.fullmatch('/data/local/tmp/sub15-wireless-[a-f0-9]{32}',remote),'owned remote directory')
    adb=['adb','-s',plan['adb_target']]
    def shell(args):return adb+['shell',shlex.join(args)]
    required={key:shell(args) for key,args in {
        'serial':['getprop','ro.serialno'],'fingerprint':['getprop','ro.build.fingerprint'],
        'battery-before':['dumpsys','battery'],'battery-after':['dumpsys','battery'],
        'device-route':['ip','route','get',plan['host_ipv4']],
        'ports-before':['ss','-lntu'],'ports-after':['ss','-lntu'],'mkdir':['mkdir',remote],
        'grammar':['timeout','-s','KILL','30',remote+'/grammar-test'],
        'cleanup':['rm','-rf',remote],'cleanup-verified':['test','!','-e',remote]}.items()}
    required['host-route']=['ip','-j','route','get',plan['headset_ipv4']]
    for i,file in enumerate(plan['files']):
        target=remote+'/'+file['remote'];required['push-'+str(i)]=adb+['push',file['local'],target]
        required['hash-'+str(i)]=shell(['sha256sum',target]);required['chmod-'+str(i)]=shell(['chmod','700',target])
    for arm in plan['optional_smoke']+plan['runs']:
        name=arm['name'];required[name+'-sender']=['timeout','-s','KILL','40']+arm['sender_argv']
        required[name+'-receiver']=shell(['timeout','-s','KILL','40']+[x.replace('{remote}',remote) for x in arm['receiver_argv']])
        required[name+'-audit']=['python3',str(auditor),str(directory/(name+'-sender.stdout')),str(directory/(name+'-receiver.stdout')),str(directory/(name+'-audit.json'))]
    return required

def check_required(commands,required):
    for label,argv in required.items():need(label in commands and commands[label]['argv']==argv,'missing or changed required control: '+label)

def check_command_population(commands,required,plan,directory):
    check_required(commands,required);covered=set(required)
    for label,command in commands.items():
        timeout=command['timeout_s'];need(type(timeout) in (int,float) and 0<timeout<=60,'command timeout bound')
        if '-ready-' in label:need(timeout<=3,'readiness deadline')
        else:
            expected=45 if label.endswith('-sender') or label.endswith('-receiver') else 60 if label.startswith('push-') else 40 if label=='grammar' else 30
            need(timeout==expected,'command timeout differs')
    for arm in plan['optional_smoke']+plan['runs']:
        prefix=arm['name']+'-ready-';labels=sorted((label for label in commands if label.startswith(prefix)),key=lambda s:int(s[len(prefix):]))
        need(1<=len(labels)<=30 and [int(label[len(prefix):]) for label in labels]==list(range(len(labels))),'readiness population')
        for label in labels:need(commands[label]['argv']==['adb','-s',plan['adb_target'],'shell','ss -lntu'],'readiness command')
        covered.update(labels);lines=(directory/(labels[-1]+'.stdout')).read_text().splitlines()
        state='LISTEN' if arm['transport']=='tcp' else 'UNCONN';local=plan['headset_ipv4']+':'+str(arm['receiver_port'])
        need(any(len(fields)>=5 and fields[0]==arm['transport'] and fields[1]==state and fields[4]==local for fields in map(str.split,lines)),'owned receiver readiness observation')
    need(set(commands)==covered,'undeclared capture commands')

def check_addresses(sender,receiver,plan):
    need(sender['local_ipv4']==plan['host_ipv4'] and sender['peer_ipv4']==plan['headset_ipv4'] and receiver['local_ipv4']==plan['headset_ipv4'] and receiver['peer_ipv4']==plan['host_ipv4'],'raw endpoints differ from routed plan')

def audit(plan_path,directory,runner_path,repo):
    before=inventory(directory);plan=json.loads(plan_path.read_text());arms=check_plan(plan)
    receipt=json.loads((directory/'receipt.json').read_text())
    need(receipt['status']=='collection_completed' and receipt['inputs_unchanged'] is True and receipt['cleanup_errors']==[] and receipt['owned_receiver_ports_absent'] is True,'capture/cleanup status')
    need(receipt['headset_used'] is True and receipt['openxr_used'] is False and receipt['promotion_eligible'] is False,'capture scope')
    need(receipt['plan_sha256']==sha(plan_path) and receipt['runner_sha256']==sha(runner_path),'control source changed')
    auditor=Path(__file__).with_name('audit_network.py');need(receipt['audit_sha256']==sha(auditor),'endpoint auditor changed')
    commands=check_logs(receipt,directory)
    first_audit=commands[EXPECTED_NAMES[0]+'-audit']['argv']
    need(len(first_audit)==5,'captured audit argv shape')
    recorded_directory=Path(first_audit[2]).parent;recorded_auditor=Path(plan['source_directory'])/'audit_network.py'
    required=required_commands(plan,receipt['remote_directory'],recorded_directory,recorded_auditor)
    check_command_population(commands,required,plan,directory)
    need({label for label in commands if label.endswith('-sender') or label.endswith('-receiver')}=={name+'-'+role for name in EXPECTED_NAMES for role in ['sender','receiver']},'endpoint execution population')
    need((directory/'serial.stdout').read_text().strip()==plan['expected_headset_serial']==receipt['device_serial'],'device serial')
    routes=json.loads((directory/'host-route.stdout').read_text());need(len(routes)==1 and routes[0]['dev']==plan['expected_host_interface'] and routes[0]['prefsrc']==plan['host_ipv4'] and routes[0]['dst']==plan['headset_ipv4'],'host physical route')
    route=(directory/'device-route.stdout').read_text();need(re.search(r'^192\.168\.1\.106 dev wlan0 .*\bsrc 192\.168\.1\.105\b',route),'headset wireless route')
    pattern=re.compile(r':(?:'+'|'.join(map(str,plan['require_free_receiver_ports']))+r')(?:\s|$)')
    for key in ['ports-before','ports-after']:need(not pattern.search((directory/(key+'.stdout')).read_text()),'owned port present outside run')
    need('cleanup-verified' in commands and (directory/'cleanup-verified.stdout').read_bytes()==b'' and (directory/'cleanup-verified.stderr').read_bytes()==b'','cleanup observation')
    for i,file in enumerate(plan['files']):
        need(sha(Path(file['local']))==file['sha256'],'local executable drift')
        need((directory/f'hash-{i}.stdout').read_text().split()==[file['sha256'],receipt['remote_directory']+'/'+file['remote']],'deployed executable bytes/path')
    need(sha(Path(plan['host_binary']['path']))==plan['host_binary']['sha256'],'host executable drift')
    grammar=json.loads((directory/'grammar.stdout').read_text())
    need(grammar=={'pass':True,'exact_records':261,'rejections':6179,'sockets_opened':False},'Android pure grammar coverage')
    need((directory/'grammar.stderr').read_bytes()==b'','Android grammar error')
    exp='prototypes/vr444/experiments/wireless_pair_probe/'
    qualification_path=repo/exp/'results/offline-qualification.json'
    qualified=json.loads(subprocess.check_output(['git','show',PROBE_COMMIT+':'+exp+'results/offline-qualification.json'],cwd=repo,text=True))
    need(sha(qualification_path)==hashlib.sha256(subprocess.check_output(['git','show',PROBE_COMMIT+':'+exp+'results/offline-qualification.json'],cwd=repo)).hexdigest(),'pinned qualification changed')
    qualified_artifacts(plan,qualified)
    documentation_differences=[];current_matches=0
    for path,h in qualified['source_sha256'].items():
        need(hashlib.sha256(subprocess.check_output(['git','show',PROBE_COMMIT+':'+path],cwd=repo)).hexdigest()==h,'pinned implementation Git source drift')
        current=sha(repo/path)
        if current==h:current_matches+=1
        else:
            need(path==exp+'README.txt','pinned implementation source drift')
            documentation_differences.append(dict(path=path,pinned_sha256=h,current_sha256=current,scope='Post-campaign documentation; measured native implementation unchanged.'))
    for binary,h in qualified['binary_sha256'].items():need(sha(Path(binary))==h,'qualified binary changed')
    need([r['name'] for r in receipt['runs']]==EXPECTED_NAMES,'receipt arm population')
    rows=[]
    for arm,run in zip(arms,receipt['runs']):
        name=arm['name'];need(run['sender_exit']==run['receiver_exit']==0,'endpoint exit')
        sender_command=commands[name+'-sender']['argv'];need(sender_command==['timeout','-s','KILL','40']+arm['sender_argv'],'executed host arguments')
        receive=commands[name+'-receiver']['argv'];need(receive[:4]==['adb','-s',plan['adb_target'],'shell'] and len(receive)==5,'executed device prefix')
        expected=['timeout','-s','KILL','40']+[x.replace('{remote}',receipt['remote_directory']) for x in arm['receiver_argv']]
        need(shlex.split(receive[4])==expected,'executed device arguments')
        s_path=directory/(name+'-sender.stdout');r_path=directory/(name+'-receiver.stdout')
        for role in ['sender','receiver']:need((directory/(name+'-'+role+'.stderr')).read_bytes()==b'','endpoint diagnostic')
        sender=json.loads(s_path.read_text());receiver=json.loads(r_path.read_text())
        check_addresses(sender,receiver,plan)
        for endpoint in [sender,receiver]:
            for field,wanted in [('transport',arm['transport']),('port',arm['receiver_port']),('run_token',arm['sender_argv'][6]),('planned_pairs',arm['frames']),('pair_bytes',arm['pair_bytes']),('hz',arm['hz'])]:need(endpoint[field]==wanted,'raw result differs from executed plan')
        result=audit_network.audit(sender,receiver)
        saved_path=directory/(name+'-audit.json');saved=json.loads(saved_path.read_text());need(sha(saved_path)==run['audit_sha256'],'saved audit changed')
        for key,value in result.items():need(saved[key]==value,'independent audit differs')
        expected_bindings={str(recorded_directory/s_path.name):sha(s_path),str(recorded_directory/r_path.name):sha(r_path),str(recorded_auditor):sha(auditor)}
        need(saved['input_sha256']==expected_bindings,'endpoint audit input drift or undeclared binding')
        need(not sender['aborted'] and not receiver['aborted'],'aborted frame population')
        need(sender['processed_pairs']==receiver['processed_pairs']==arm['frames'],'omitted endpoint frames')
        need(sender['complete_pairs']==receiver['complete_pairs']==arm['frames'],'incomplete pair')
        need(sender['rejected_records']==receiver['rejected_records']==sender['stale_records']==receiver['stale_records']==0,'unexpected rejection/stale traffic')
        need(all(r['duplicates']==0 for r in receiver['rows']),'unexpected duplicates')
        n=sender['data_records_per_pair'];prefix=2 if arm['transport']=='tcp' else 0
        expected_data=arm['frames']*(arm['pair_bytes']+n*(40+prefix));expected_ack=arm['frames']*(56+prefix)
        need(sender['records_sent']==arm['frames']*n and receiver['records_sent']==arm['frames'],'source/ACK record counts')
        need(sender['application_bytes_sent']==receiver['application_bytes_received']==expected_data and receiver['application_bytes_sent']==sender['application_bytes_received']==expected_ack,'clean actual socket byte accounting')
        rows.append(dict(name=name,smoke=name.startswith('smoke'),pair_bytes=arm['pair_bytes'],requested_hz=arm['hz'],nominal_ipv4_udp_offered_mbps=arm['nominal_ipv4_udp_offered_mbps'],**result))
    need(before==inventory(directory),'capture files changed during audit')
    return dict(schema=1,status='audited',probe_commit=PROBE_COMMIT,main_pairs=sum(r['planned_pairs'] for r in rows if not r['smoke']),smoke_pairs=sum(r['planned_pairs'] for r in rows if r['smoke']),total_exact_pairs=sum(r['exact_receiver_pairs'] for r in rows),main_endpoint_processes=16,total_endpoint_processes=20,
        path='Host192.168.1.106 enp113s0 to Quest192.168.1.105 wlan0; reciprocal captured route evidence, no ADB forwarding used for payloads.',
        command_logs_checked=len(commands),capture_files_checked=len(before),pinned_source_bindings=len(qualified['source_sha256']),current_matching_source_bindings=current_matches,documented_snapshot_differences=documentation_differences,inputs_unchanged=True,owned_ports_absent_after=True,cleanup_verified=True,
        input_sha256={str(p):sha(p) for p in [plan_path,directory/'receipt.json',runner_path,Path(__file__),auditor,qualification_path]},capture_sha256=before,rows=rows,
        selection='Keep TCP baseline. UDP is compatible on these finite complete-delivery probes, without a compelling repeated RTT-tail advantage. Do not select FEC/batching/offload from an ordinary UDP probe; native negotiated adapter and sustained complete-stereo cadence remain separate.',
        clock_scope='Sender RTT and receiver-local assembly/cadence, each within its own clock. No one-way cross-clock or earliest-headset-receipt-to-xrEndFrame/optical metric.',promotion_eligible=False)
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('plan',type=Path);p.add_argument('directory',type=Path);p.add_argument('runner',type=Path);p.add_argument('repository',type=Path);p.add_argument('output',type=Path);a=p.parse_args()
    result=audit(a.plan,a.directory,a.runner,a.repository);a.output.write_text(json.dumps(result,indent=2)+'\n');print(json.dumps({k:result[k] for k in ['status','main_pairs','smoke_pairs','total_exact_pairs','command_logs_checked','capture_files_checked','pinned_source_bindings']}))
