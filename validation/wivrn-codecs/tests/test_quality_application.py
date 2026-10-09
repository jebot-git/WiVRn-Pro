import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

PATH=Path(__file__).parents[1]/'integration'/'quality_application.py'
SPEC=importlib.util.spec_from_file_location('quality_application_test',PATH)
qa=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(qa)

class QualityApplication(unittest.TestCase):
    def fixture(self,root):
        exe=root/'probe';exe.write_bytes(b'\x7fELF'+bytes(range(256)))
        plan=dict(schema=1,profile='application-quality-stimuli',latency_comparable=False,
          source_generation='direct_encoded_srgb_byte_upload',pattern='chroma-grid',source_stage='application_swapchain_before_compositor',
          width=1800,height=1980,frame_limit=1800,seconds_limit=20,capture_directory=str(root/'run'/'app-source'),
          executable=str(exe),executable_sha256=qa.digest(exe.read_bytes()),original_executable=str(root/'original'),
          gpu_or_runtime_started=False,required_native_contract=qa.REQUIRED.copy(),
          source_fov_order=['left','right','up','down'],quality_mapping_fov_order=['left','right','down','up'],limitations=[])
        plan['application']=qa.argv(plan,exe)
        path=root/'plan.json';path.write_text(json.dumps(plan))
        args=SimpleNamespace(quality_application_plan=path,quality_application_plan_sha256=qa.digest(path.read_bytes()),
            out=root/'run',mode='custom-rrb32',rrb_source='q1',q=1,compression='none',source_trace=True,quality_capture=True,
            decoder_backend='cpu-neon',cpu_output='cached-copy',rrb_workers=4,rrb_output='cpu',seconds=30)
        return args,plan

    def test_no_plan_is_no_application_override(self):
        self.assertIsNone(qa.preflight(SimpleNamespace()))
        self.assertEqual(qa.configure(None,None,None),(None,None))
        with self.assertRaises(ValueError):qa.preflight(SimpleNamespace(quality_application_plan_sha256='a'*64))

    def test_cached_exact_bytes_and_private_runtime_argv(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,plan=self.fixture(root);cached=qa.preflight(args)
            Path(plan['executable']).write_bytes(b'changed original');Path(args.quality_application_plan).write_text('{}')
            runtime=root/'runtime';runtime.mkdir()
            record,command=qa.configure(cached,runtime,args.out)
            self.assertEqual(qa.digest(Path(command[7]).read_bytes()),plan['executable_sha256'])
            self.assertEqual(command[:4],['/usr/bin/timeout','-k','2','30'])
            self.assertFalse(record['latency_eligible']);self.assertEqual(record['source_application_space'],'VIEW')
            self.assertEqual((args.out/'quality-application-plan.json').read_bytes(),cached['plan_bytes'])
            self.assertFalse((args.out/'app-source').exists())

    def test_explicit_space_is_in_plan_snapshot_and_command(self):
        for space in ('LOCAL','VIEW'):
            with tempfile.TemporaryDirectory() as temp:
                root=Path(temp);args,plan=self.fixture(root)
                plan.update(schema=2,source_reference_space=space);plan['application']=qa.argv(plan,plan['executable'])
                args.quality_application_plan.write_text(json.dumps(plan));args.quality_application_plan_sha256=qa.digest(args.quality_application_plan.read_bytes())
                cached=qa.preflight(args);runtime=root/'runtime';runtime.mkdir();record,command=qa.configure(cached,runtime,args.out)
                self.assertEqual(record['source_application_space'],space);self.assertEqual(record['source_reference_space'],space)
                self.assertEqual(command[command.index('--source-reference-space')+1],space)

    def test_rehashed_space_cannot_disagree_with_argv_or_relabel_legacy(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,legacy=self.fixture(root)
            local=copy.deepcopy(legacy);local.update(schema=2,source_reference_space='LOCAL');local['application']=qa.argv(local,local['executable'])
            bad=[]
            p=copy.deepcopy(local);p['source_reference_space']='VIEW';bad.append(p)
            p=copy.deepcopy(local);p['source_reference_space']='STAGE';bad.append(p)
            p=copy.deepcopy(local);del p['source_reference_space'];bad.append(p)
            p=copy.deepcopy(legacy);p['source_reference_space']='LOCAL';bad.append(p)
            for plan in bad:
                args.quality_application_plan.write_text(json.dumps(plan));args.quality_application_plan_sha256=qa.digest(args.quality_application_plan.read_bytes())
                with self.assertRaises(ValueError):qa.preflight(args)

    def test_native_scope_negative_matrix(self):
        changes={'mode':'custom-vr444','rrb_source':'vr444','q':8,'compression':'lz4','source_trace':False,'quality_capture':False,
                 'decoder_backend':'resident','cpu_output':'mapped-direct','rrb_workers':3,'rrb_output':'other','seconds':24}
        with tempfile.TemporaryDirectory() as temp:
            args,_=self.fixture(Path(temp))
            for key,value in changes.items():
                bad=copy.copy(args);setattr(bad,key,value)
                with self.assertRaises(ValueError,msg=key):qa.preflight(bad)

    def test_rehashed_plan_cannot_substitute_host_command(self):
        mutations=[lambda p:p['application'].__setitem__(0,'/bin/sh'),lambda p:p['application'].__setitem__(7,'/bin/true'),
                   lambda p:p['application'].extend(['--unbounded']),lambda p:p.update(capture_directory='/tmp/wrong'),
                   lambda p:p.update(latency_comparable=True),lambda p:p.update(width=4096,height=4096),
                   lambda p:p['required_native_contract'].update(q=8),lambda p:p.update(pattern='arbitrary'),
                   lambda p:p.update(schema=True),lambda p:p.update(source_stage='client_decode')]
        with tempfile.TemporaryDirectory() as temp:
            args,plan=self.fixture(Path(temp))
            for mutate in mutations:
                p=copy.deepcopy(plan);mutate(p);args.quality_application_plan.write_text(json.dumps(p));args.quality_application_plan_sha256=qa.digest(args.quality_application_plan.read_bytes())
                with self.assertRaises(ValueError):qa.preflight(args)

    def test_binary_pin_symlink_and_changed_output_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,plan=self.fixture(root)
            bad=copy.copy(args);bad.quality_application_plan_sha256='a'*64
            with self.assertRaises(ValueError):qa.preflight(bad)
            cached=qa.preflight(args);runtime=root/'runtime';runtime.mkdir()
            with self.assertRaises(ValueError):qa.configure(cached,runtime,root/'wrong-output')
            exe=Path(plan['executable']);data=exe.read_bytes();exe.write_bytes(data+b'x')
            with self.assertRaises(ValueError):qa.preflight(args)
            other=root/'other';other.write_bytes(data);exe.unlink();exe.symlink_to(other)
            with self.assertRaises(ValueError):qa.preflight(args)

    def test_duplicate_json_and_tampered_cached_bytes_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);args,_=self.fixture(root);cached=qa.preflight(args);cached['binary_bytes']+=b'x'
            runtime=root/'runtime';runtime.mkdir()
            with self.assertRaises(ValueError):qa.configure(cached,runtime,args.out)
            raw=args.quality_application_plan.read_text();args.quality_application_plan.write_text(raw[:-1]+',"schema":1}')
            args.quality_application_plan_sha256=qa.digest(args.quality_application_plan.read_bytes())
            with self.assertRaises(ValueError):qa.preflight(args)

if __name__=='__main__':unittest.main()
