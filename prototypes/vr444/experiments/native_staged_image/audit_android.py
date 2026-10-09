#!/usr/bin/env python3
"""Read-only audit of the fixed standalone Android helper plan; no ADB calls."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shlex
import subprocess

QUALIFIED = '1b3541c91cb77eff6ba032052b254f48fc86d2a2'
PLAN_SHA = '116767292486413b8e4b6caba5db6a0f7c655996680784d24f7b20be1e171350'
RUNNER_SHA = 'e495081a5bdbbef3aa94cff0082b700516740dca711d10d3ec9dedb143e4c64a'
EXP = 'prototypes/vr444/experiments/native_staged_image/'


def need(value, message):
    if not value:
        raise ValueError(message)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def rows(raw, gpu):
    parsed = [json.loads(line) for line in raw.splitlines()]
    if gpu:
        shapes = [(1,1),(1,129),(17,33),(129,65),(4095,3),(4096,1),(1,4096),(576,640)]
        need(len(parsed) == 9 and [(r['width'],r['height']) for r in parsed[:-1]] == shapes, 'GPU case population')
        identity = None
        for case in parsed[:-1]:
            need(all(type(case[key]) is int for key in ('width','height','device_type','vendor_id','device_id','driver','api_version','queue_family')),
                 'GPU integer field types')
            need(type(case['device']) is str and bool(case['device']), 'GPU identity string')
            need(case['device_type'] in (1,2) and case['unorm_storage_srgb_linear_supported'] is True,
                 'hardware capability required')
            current = {key:case[key] for key in ('device','device_type','vendor_id','device_id','driver','api_version','queue_family')}
            need(identity is None or identity == current, 'GPU identity changed')
            identity = current
    else:
        need(len(parsed) == 1, 'CPU result population')
    result = parsed[-1]
    need(type(result.get('checks')) is int and all(type(result.get(key)) is bool for key in
         ('pass','gpu_used','headset_used','performance_claim','native_renderer_executed')), 'result field types')
    need(result == {'pass':True,'checks':826 if gpu else 66,'gpu_used':gpu,
                    'headset_used':False,'performance_claim':False,'native_renderer_executed':False},
         'standalone result population or scope changed')
    return parsed


def audit(root, directory, plan_path, runner_path):
    files = list(directory.iterdir())
    need(all(p.is_file() and not p.is_symlink() for p in files), 'flat regular capture files required')
    # Parse the same immutable buffers that were hashed, never a later reread.
    captured = {p.name:p.read_bytes() for p in files}
    inventory = {name:hashlib.sha256(raw).hexdigest() for name,raw in captured.items()}
    plan_bytes = plan_path.read_bytes()
    source_hash = sha(Path(__file__))
    need(hashlib.sha256(plan_bytes).hexdigest() == PLAN_SHA and sha(runner_path) == RUNNER_SHA, 'fixed plan or runner changed')
    plan = json.loads(plan_bytes)
    receipt = json.loads(captured['receipt.json'])
    need(receipt['status'] == 'completed' and receipt['inputs_unchanged'] is True
         and receipt['headset_used'] is True and receipt['openxr_used'] is False
         and receipt['performance_qualification'] is False and receipt['promotion_eligible'] is False,
         'collection incomplete or scope changed')
    need(receipt['plan_sha256'] == PLAN_SHA and receipt['runner_sha256'] == RUNNER_SHA
         and receipt['inputs'] == plan['files'], 'collection provenance changed')
    qualified_bytes = subprocess.check_output(['git','show',QUALIFIED+':'+EXP+'results/qualification.json'],cwd=root)
    qualified = json.loads(qualified_bytes)
    need(qualified['status'] == 'pass' and qualified['sources_unchanged'] is True
         and qualified['android_build_only'] is True, 'historical compile qualification')
    for path, digest in qualified['source_sha256'].items():
        blob = subprocess.check_output(['git','show',QUALIFIED+':'+path],cwd=root)
        need(hashlib.sha256(blob).hexdigest() == digest, 'historical source binding')
    for file in plan['files']:
        need(qualified['binary_sha256'][file['local']] == file['sha256'] == sha(Path(file['local'])),
             'deployed bytes differ from qualified binary/shader')
    remote = receipt['remote_directory']
    need(re.fullmatch('/data/local/tmp/sub15-review-[a-f0-9]{32}',remote), 'owned remote path')
    adb = ['adb','-s',plan['adb_target']]
    def shell(argv):
        return adb+['shell',shlex.join(argv)]
    required = {label:(shell(argv),30) for label,argv in {
        'serial':['getprop','ro.serialno'], 'fingerprint':['getprop','ro.build.fingerprint'],
        'battery-before':['dumpsys','battery'], 'mkdir':['mkdir',remote]}.items()}
    parents = set()
    for i,file in enumerate(plan['files']):
        destination = remote+'/'+file['remote']; parent = str(Path(destination).parent)
        if parent != remote and parent not in parents:
            required['mkdir-'+str(i)] = (shell(['mkdir','-p',parent]),30);parents.add(parent)
        required['push-'+str(i)] = (adb+['push',file['local'],destination],60)
        required['hash-'+str(i)] = (shell(['sha256sum',destination]),30)
        if file.get('executable'):
            required['chmod-'+str(i)] = (shell(['chmod','700',destination]),30)
        need(captured[f'hash-{i}.stdout'].decode().split() == [file['sha256'],destination],
             'deployed digest/path mismatch')
    for run in plan['runs']:
        argv = [x.replace('{remote}',remote) for x in run['argv']]
        required[run['name']] = (shell(['timeout','-s','KILL',str(run['timeout_s'])]+argv),run['timeout_s']+15)
    for label,argv in {'battery-after':['dumpsys','battery'], 'cleanup':['rm','-rf',remote],
                       'cleanup-verified':['test','!','-e',remote]}.items():
        required[label] = (shell(argv),30)
    commands = receipt['commands']
    need([c['label'] for c in commands] == list(required), 'command population/order')
    need(set(inventory) == {'receipt.json'} | {label+'.'+stream for label in required for stream in ('stdout','stderr')},
         'capture file population')
    for command in commands:
        argv, timeout = required[command['label']]
        need(command['argv'] == argv and command['timeout_s'] == timeout, 'command arguments or timeout')
        need(type(command['returncode']) is int and command['returncode'] == 0
             and command.get('host_timeout',False) is False, 'failed acquisition command')
        for stream in ('stdout','stderr'):
            need(command[stream+'_sha256'] == inventory[command['label']+'.'+stream], 'raw command digest')
    need(receipt['device_serial'] == plan['expected_serial'] == captured['serial.stdout'].decode().strip(), 'actual headset identity')
    need(captured['cleanup-verified.stdout'] == captured['cleanup-verified.stderr'] == b'', 'cleanup verification')
    observations = {}
    for label,gpu in [('staged-cpu-guards',False),('staged-image-consumer',True)]:
        need(captured[label+'.stderr'] == b'', 'native diagnostic output')
        observations[label] = rows(captured[label+'.stdout'],gpu)
    need(inventory == {p.name:sha(p) for p in directory.iterdir() if p.is_file()}, 'capture changed during audit')
    need(sha(plan_path) == PLAN_SHA and sha(runner_path) == RUNNER_SHA
         and sha(Path(__file__)) == source_hash, 'control source changed during audit')
    return dict(schema=1,status='pass',qualified_commit=QUALIFIED,
                source_bindings=len(qualified['source_sha256']),uploaded_artifacts=len(plan['files']),
                command_logs=len(commands),observations=observations,capture_sha256=inventory,
                input_sha256={str(plan_path):PLAN_SHA,str(runner_path):RUNNER_SHA,
                              str(Path(__file__)):source_hash,str(directory/'receipt.json'):inventory['receipt.json']},
                actual_headset_used=True,headset_serial=receipt['device_serial'],openxr_used=False,
                raw_host_scope_labels_preserved=True,full_goal_qualified=False,promotion_eligible=False,
                scope='Actual-device private staged-image and first-consumer compatibility/correctness; no native ownership integration or latency claim')


if __name__ == '__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('directory',type=Path);p.add_argument('output',type=Path)
    p.add_argument('--runner',type=Path,required=True)
    args=p.parse_args();here=Path(__file__).resolve().parent
    result=audit(here.parents[3],args.directory,here/'android-plan.json',args.runner)
    args.output.write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:result[k] for k in ('status','uploaded_artifacts','command_logs','actual_headset_used','full_goal_qualified')}))
