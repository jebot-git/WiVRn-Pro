import base64,copy,hashlib,json,shlex,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import capture
from qualify import job
HERE=Path(__file__).resolve().parent
NATIVE=Path('/home/blux/spatial-bridge/.worktrees/sub15-dual-goal/validation/results/dual-goal-20261009/build-receipt.json')
NATIVE_SHA='d6be1ee952a93a967cc6d3492edd89abe7e8fa49e25ad77df0f37dcbee82ec8f'
RUNNER=capture.ROOT/'prototypes/vr444/experiments/headset_review_smoke/run.py'
def put(path,value):path.write_text(json.dumps(value))
def sha(raw):return hashlib.sha256(raw).hexdigest()
def controls():
    shaders,_=capture.renderer(NATIVE,NATIVE_SHA)
    return dict(build_receipt_sha256=NATIVE_SHA,candidate_capture_audit=dict(headset_serial_recorded='SYNTHETIC_ONLY'),renderer_shaders={name:dict(sha256=sha(code),literal_equivalence_recomputed=True,actual_bytes=len(code))for name,code in shaders.items()})
class ReplayCapture(unittest.TestCase):
    def plan(self,root):
        raw,_=job([1],[1]);(root/'source.rlp').write_bytes(raw)
        adapter=dict(status='pass',job=dict(path='source.rlp',sha256=sha(raw),bytes=len(raw),format='RLP1',version=1),output=dict(width=1,height=1,vk_format=43),**controls())
        path=root/'adapter.json';put(path,adapter)
        plan=capture.prepare(path,sha(path.read_bytes()),NATIVE,NATIVE_SHA,root/'bundle','192.0.2.1:5555','SYNTHETIC_ONLY')
        return plan,root/'bundle/plan.json'
    def fixture(self,root):
        plan,path=self.plan(root);directory=root/'device';directory.mkdir();remote='/data/local/tmp/sub15-review-'+'a'*32;adb=['adb','-s',plan['adb_target']];commands=[]
        def add(label,argv,out=b'',timeout=30):
            (directory/(label+'.stdout')).write_bytes(out);(directory/(label+'.stderr')).write_bytes(b'')
            commands.append(dict(label=label,argv=argv,timeout_s=timeout,returncode=0,stdout_sha256=sha(out),stderr_sha256=sha(b'')))
        def shell(label,argv,out=b'',timeout=30):add(label,adb+['shell',shlex.join(argv)],out,timeout)
        shell('serial',['getprop','ro.serialno'],b'SYNTHETIC_ONLY\n');shell('fingerprint',['getprop','ro.build.fingerprint'],b'SYNTHETIC UNIT TEST\n');shell('battery-before',['dumpsys','battery']);shell('mkdir',['mkdir',remote])
        for i,f in enumerate(plan['files']):
            dest=remote+'/'+f['remote'];add('push-'+str(i),adb+['push',f['local'],dest],timeout=60);shell('hash-'+str(i),['sha256sum',dest],(f['sha256']+'  '+dest+'\n').encode())
            if f.get('executable'):shell('chmod-'+str(i),['chmod','700',dest])
        left=bytes([12,34,56,255]);right=bytes([98,76,54,255]);omitted=b'\x5a'*4
        record=dict(replay_completed=True,eyes=2,device='SYNTHETIC GPU',device_type=1,vendor_id=1,device_id=2,driver=3,api_version=4198400,queue_family=0,width=1,height=1,vk_format=43,openxr_used=False,quality_qualified=False,performance_claim=False)
        results={'replay-guards':json.dumps(dict(job_valid=True,eyes=2,gpu_used=False)).encode(),
                 'replay-render':json.dumps(record|dict(omission_control=False)).encode(),
                 'replay-omission':json.dumps(record|dict(omission_control=True)).encode(),
                 'left-bytes':base64.encodebytes(left),'right-bytes':base64.encodebytes(right),'omission-right-bytes':base64.encodebytes(omitted)}
        for name,data,file in [('left-hash',left,'render-eye-0.raw'),('right-hash',right,'render-eye-1.raw'),('omission-left-hash',left,'omitted-eye-0.raw'),('omission-right-hash',omitted,'omitted-eye-1.raw')]:results[name]=(sha(data)+'  '+remote+'/'+file+'\n').encode()
        for run in plan['runs']:shell(run['name'],['timeout','-s','KILL',str(run['timeout_s'])]+[x.replace('{remote}',remote)for x in run['argv']],results[run['name']],run['timeout_s']+15)
        shell('battery-after',['dumpsys','battery']);shell('cleanup',['rm','-rf',remote]);shell('cleanup-verified',['test','!','-e',remote])
        receipt=dict(status='completed',inputs_unchanged=True,headset_used=True,openxr_used=False,performance_qualification=False,promotion_eligible=False,remote_directory=remote,device_serial='SYNTHETIC_ONLY',inputs=plan['files'],commands=commands,plan_sha256=sha(path.read_bytes()),runner_sha256=capture.RUNNER_SHA)
        put(directory/'receipt.json',receipt);return directory,path,receipt
    def invoke(self,root,directory,plan):return capture.audit(directory,plan,RUNNER,root/'out')
    def mutate_stdout(self,directory,receipt,label,raw):
        (directory/(label+'.stdout')).write_bytes(raw);next(x for x in receipt['commands']if x['label']==label)['stdout_sha256']=sha(raw);put(directory/'receipt.json',receipt)
    def test_complete_synthetic_acquisition(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);directory,plan,_=self.fixture(root);r=self.invoke(root,directory,plan)
            self.assertEqual(r['command_count'],26);self.assertFalse(r['quality_qualified']);self.assertTrue(r['omission_control_verified']);self.assertEqual((root/'out/eye-1.raw').read_bytes(),bytes([98,76,54,255]))
    def test_failed_missing_reordered_timeout_commands(self):
        for mode in range(4):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory()as t:
                root=Path(t);directory,plan,r=self.fixture(root)
                if mode==0:r['commands'][4]['returncode']=1
                if mode==1:r['commands'].pop(4)
                if mode==2:r['commands'][4],r['commands'][5]=r['commands'][5],r['commands'][4]
                if mode==3:r['commands'][4]['host_timeout']=True
                put(directory/'receipt.json',r)
                with self.assertRaises(ValueError):self.invoke(root,directory,plan)
    def test_output_hash_path_and_decoding_controls(self):
        for label,data in [('right-bytes',b'YWJjZA=='),('right-hash',(sha(bytes([98,76,54,255]))+'  /wrong/path\n').encode()),('left-bytes',b'!A=='),('right-bytes',b''),('omission-right-bytes',base64.encodebytes(b'ZZZX'))]:
            with self.subTest(label=label,data=data),tempfile.TemporaryDirectory()as t:
                root=Path(t);directory,plan,r=self.fixture(root);self.mutate_stdout(directory,r,label,data)
                with self.assertRaises((ValueError,base64.binascii.Error)):self.invoke(root,directory,plan)
    def test_result_scope_hardware_and_boolean_types(self):
        for change in [dict(device_type=4),dict(device_type=True),dict(quality_qualified=True),dict(omission_control=True),dict(width=2),dict(eyes=True)]:
            with self.subTest(change=change),tempfile.TemporaryDirectory()as t:
                root=Path(t);directory,plan,r=self.fixture(root);row=json.loads((directory/'replay-render.stdout').read_bytes());row.update(change);self.mutate_stdout(directory,r,'replay-render',json.dumps(row).encode())
                with self.assertRaises(ValueError):self.invoke(root,directory,plan)
    def test_unbound_extra_symlink_and_changed_snapshot(self):
        for mode in range(3):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory()as t:
                root=Path(t);directory,plan,r=self.fixture(root)
                if mode==0:(directory/'extra').write_text('x')
                if mode==1:
                    target=directory/'fingerprint.stdout';data=target.read_bytes();target.unlink();(root/'other').write_bytes(data);target.symlink_to(root/'other')
                if mode==2:
                    (root/'bundle/job.rlp').chmod(0o600);(root/'bundle/job.rlp').write_bytes(b'changed')
                with self.assertRaises(ValueError):self.invoke(root,directory,plan)
    def test_forged_uploaded_shader_cannot_bypass_native_join(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);directory,plan,r=self.fixture(root);p=json.loads(plan.read_bytes());f=p['files'][1];raw=Path(f['local']).read_bytes()+b'xxxx';Path(f['local']).chmod(0o600);Path(f['local']).write_bytes(raw);f['sha256']=sha(raw);put(plan,p);r['inputs']=p['files'];r['plan_sha256']=sha(plan.read_bytes());self.mutate_stdout(directory,r,'hash-1',(f['sha256']+'  '+r['remote_directory']+'/'+f['remote']+'\n').encode())
            with self.assertRaisesRegex(ValueError,'native shader join'):self.invoke(root,directory,plan)
    def test_plan_rejects_native_job_grammar_and_adapter_identity(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);raw,_=job([1],[1]);raw+=b'X';(root/'source.rlp').write_bytes(raw);adapter=dict(status='pass',job=dict(path='source.rlp',sha256=sha(raw),bytes=len(raw),format='RLP1',version=1),output=dict(width=1,height=1,vk_format=43),**controls());path=root/'adapter.json';put(path,adapter)
            with self.assertRaisesRegex(ValueError,'grammar admission'):capture.prepare(path,sha(path.read_bytes()),NATIVE,NATIVE_SHA,root/'bundle','192.0.2.1:5555','SYNTHETIC_ONLY')
    def test_adapter_rejects_cross_build_shader_and_headset_splices(self):
        for mode in range(4):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory()as t:
                root=Path(t);raw,_=job([1],[1]);(root/'source.rlp').write_bytes(raw)
                adapter=dict(status='pass',job=dict(path='source.rlp',sha256=sha(raw),bytes=len(raw),format='RLP1',version=1),output=dict(width=1,height=1,vk_format=43),**controls())
                if mode==0:adapter['build_receipt_sha256']='0'*64
                if mode==1:adapter['candidate_capture_audit']['headset_serial_recorded']='OTHER_HEADSET'
                if mode==2:adapter['renderer_shaders']['reprojection.vert']['sha256']='0'*64
                if mode==3:adapter['renderer_shaders']['reprojection.frag']['literal_equivalence_recomputed']=1
                path=root/'adapter.json';put(path,adapter)
                with self.assertRaises(ValueError):capture.prepare(path,sha(path.read_bytes()),NATIVE,NATIVE_SHA,root/'bundle','192.0.2.1:5555','SYNTHETIC_ONLY')
    def test_audit_rejects_unbound_source_parser_and_controls(self):
        for mode in range(3):
            with self.subTest(mode=mode),tempfile.TemporaryDirectory()as t:
                root=Path(t);directory,plan,r=self.fixture(root);p=json.loads(plan.read_bytes())
                if mode==0:p['replay']['planner_sha256']='0'*64
                if mode==1:p['replay']['host_parser_sha256']='0'*64
                if mode==2:
                    extra=root/'unbound';extra.write_bytes(b'unbound');p['replay']['control_sha256'][str(extra)]=sha(b'unbound')
                put(plan,p);r['plan_sha256']=sha(plan.read_bytes());put(directory/'receipt.json',r)
                with self.assertRaises(ValueError):self.invoke(root,directory,plan)
    def test_audit_rejects_validly_rehashed_adapter_identity_splice(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);directory,plan,r=self.fixture(root);p=json.loads(plan.read_bytes());adapter_path=root/'adapter.json';adapter=json.loads(adapter_path.read_bytes())
            adapter['candidate_capture_audit']['headset_serial_recorded']='OTHER_HEADSET';put(adapter_path,adapter);changed=sha(adapter_path.read_bytes())
            p['replay']['source_adapter_sha256']=changed;p['replay']['control_sha256'][str(adapter_path)]=changed;put(plan,p);r['plan_sha256']=sha(plan.read_bytes());put(directory/'receipt.json',r)
            with self.assertRaisesRegex(ValueError,'headset identity splice'):self.invoke(root,directory,plan)
    def test_replace_restore_raw_capture_uses_hashed_snapshot(self):
        with tempfile.TemporaryDirectory()as t:
            root=Path(t);directory,plan,r=self.fixture(root);target=directory/'right-bytes.stdout';before=target.read_bytes();original=capture.read;replaced=False
            def swap(path,*args,**kwargs):
                nonlocal replaced
                if Path(path)==target:
                    if replaced:target.write_bytes(before)
                    data=original(path,*args,**kwargs)
                    if not replaced:target.write_bytes(base64.encodebytes(b'xxxx'));replaced=True
                    return data
                return original(path,*args,**kwargs)
            with patch.object(capture,'read',side_effect=swap):result=self.invoke(root,directory,plan)
            self.assertEqual(result['eyes'][1]['sha256'],sha(bytes([98,76,54,255])))
if __name__=='__main__':unittest.main()
