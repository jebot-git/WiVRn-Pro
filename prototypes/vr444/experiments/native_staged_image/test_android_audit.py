import copy
import hashlib
import json
from pathlib import Path
import shlex
import tempfile
import unittest
from unittest.mock import patch

import audit_android as audit

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]


class AndroidAudit(unittest.TestCase):
    def fixture(self, directory):
        plan = json.loads((HERE/'android-plan.json').read_text())
        remote = '/data/local/tmp/sub15-review-'+'1'*32
        adb = ['adb','-s',plan['adb_target']]
        commands = []

        def add(label, argv, stdout=b'', timeout=30):
            (directory/(label+'.stdout')).write_bytes(stdout)
            (directory/(label+'.stderr')).write_bytes(b'')
            commands.append(dict(label=label,argv=argv,timeout_s=timeout,returncode=0,
                                 stdout_sha256=hashlib.sha256(stdout).hexdigest(),
                                 stderr_sha256=hashlib.sha256(b'').hexdigest()))

        def shell(label, argv, stdout=b'', timeout=30):
            add(label,adb+['shell',shlex.join(argv)],stdout,timeout)

        shell('serial',['getprop','ro.serialno'],(plan['expected_serial']+'\n').encode())
        shell('fingerprint',['getprop','ro.build.fingerprint'],b'SYNTHETIC UNIT TEST ONLY\n')
        shell('battery-before',['dumpsys','battery'])
        shell('mkdir',['mkdir',remote])
        parents=set()
        for i,file in enumerate(plan['files']):
            destination=remote+'/'+file['remote'];parent=str(Path(destination).parent)
            if parent!=remote and parent not in parents:
                shell('mkdir-'+str(i),['mkdir','-p',parent]);parents.add(parent)
            add('push-'+str(i),adb+['push',file['local'],destination],timeout=60)
            shell('hash-'+str(i),['sha256sum',destination],(file['sha256']+'  '+destination+'\n').encode())
            if file.get('executable'):shell('chmod-'+str(i),['chmod','700',destination])
        for run in plan['runs']:
            name='cpu-asan' if run['name']=='staged-cpu-guards' else 'gpu'
            raw=(HERE/'results'/(name+'.stdout')).read_bytes()
            shell(run['name'],['timeout','-s','KILL',str(run['timeout_s'])]+[x.replace('{remote}',remote) for x in run['argv']],raw,run['timeout_s']+15)
        shell('battery-after',['dumpsys','battery'])
        shell('cleanup',['rm','-rf',remote])
        shell('cleanup-verified',['test','!','-e',remote])
        receipt=dict(status='completed',inputs_unchanged=True,headset_used=True,openxr_used=False,
                     performance_qualification=False,promotion_eligible=False,plan_sha256=audit.PLAN_SHA,
                     runner_sha256=audit.RUNNER_SHA,inputs=plan['files'],remote_directory=remote,
                     device_serial=plan['expected_serial'],commands=commands)
        (directory/'receipt.json').write_text(json.dumps(receipt))
        return receipt

    def invoke(self, directory):
        # Same exact existing runner is checked locally; no invocation of it.
        runner=ROOT/'prototypes/vr444/experiments/headset_review_smoke/run.py'
        return audit.audit(ROOT,directory,HERE/'android-plan.json',runner)

    def test_complete_synthetic_capture_and_negative_command_controls(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);receipt=self.fixture(directory)
            result=self.invoke(directory)
            self.assertEqual(result['command_logs'],23)
            self.assertFalse(result['full_goal_qualified'])
            for mutate in (
                lambda r:r['commands'].pop(0),
                lambda r:r['commands'][0].update(returncode=1),
                lambda r:r['commands'][0].update(host_timeout=True),
                lambda r:r['commands'][0].update(timeout_s=99),
                lambda r:r['commands'][0].update(argv=['false']),
                lambda r:r.update(inputs_unchanged=False),
                lambda r:r.update(device_serial='wrong'),
                lambda r:r.update(remote_directory='/data/local/tmp/unowned'),
            ):
                bad=copy.deepcopy(receipt);mutate(bad)
                (directory/'receipt.json').write_text(json.dumps(bad))
                with self.assertRaises((ValueError,KeyError)):self.invoke(directory)

    def test_raw_digest_and_deployed_name_are_bound(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);receipt=self.fixture(directory)
            path=directory/'hash-0.stdout';path.write_bytes(path.read_bytes().replace(b'/staged-image-test',b'/different-file'))
            # Even a coherent rehashed command cannot change the deployed name.
            command=next(c for c in receipt['commands'] if c['label']=='hash-0')
            command['stdout_sha256']=audit.sha(path)
            (directory/'receipt.json').write_text(json.dumps(receipt))
            with self.assertRaisesRegex(ValueError,'deployed digest/path'):self.invoke(directory)

    def test_result_population_and_scope(self):
        raw=(HERE/'results/gpu.stdout').read_bytes()
        parsed=[json.loads(line) for line in raw.splitlines()]
        audit.rows(raw,True)
        mutations=[lambda r:r.pop(0),lambda r:r[-1].update(checks=825),
                   lambda r:r[-1].update(performance_claim=True),lambda r:r[0].update(device_type=4),
                   lambda r:r[0].update(width=2),lambda r:r[0].update(driver=1),
                   lambda r:r[-1].update({"pass":1}),lambda r:r[-1].update(gpu_used=1),
                   lambda r:r[0].update(device_type=True)]
        for change in mutations:
            value=copy.deepcopy(parsed);change(value)
            with self.assertRaises(ValueError):audit.rows('\n'.join(map(json.dumps,value)),True)

    def test_unqualified_artifact_identity_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory)
            original=audit.subprocess.check_output
            def changed(argv,**kwargs):
                raw=original(argv,**kwargs)
                if argv[-1].endswith('results/qualification.json'):
                    q=json.loads(raw);name=next(k for k in q['binary_sha256'] if k.endswith('staged-image-test-android'))
                    q['binary_sha256'][name]='0'*64;return json.dumps(q).encode()
                return raw
            with patch.object(audit.subprocess,'check_output',side_effect=changed):
                with self.assertRaisesRegex(ValueError,'qualified binary/shader'):self.invoke(directory)

    def test_replace_restore_cannot_change_the_parsed_hashed_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory=Path(tmp);self.fixture(directory)
            target=directory/'staged-image-consumer.stdout'
            original=target.read_bytes();changed=original.replace(b'"checks":826',b'"checks":825')
            self.assertNotEqual(original,changed)
            read=Path.read_bytes;parse=audit.rows;replaced=False
            def switch(path):
                nonlocal replaced
                data=read(path)
                if path==target and not replaced:
                    replaced=True;target.write_bytes(changed)
                return data
            def parse_snapshot(raw,gpu):
                if gpu:
                    self.assertEqual(raw,original)
                    target.write_bytes(original)
                return parse(raw,gpu)
            with patch.object(Path,'read_bytes',switch),patch.object(audit,'rows',side_effect=parse_snapshot):
                result=self.invoke(directory)
            self.assertEqual(result['observations']['staged-image-consumer'][-1]['checks'],826)


if __name__=='__main__':unittest.main()
