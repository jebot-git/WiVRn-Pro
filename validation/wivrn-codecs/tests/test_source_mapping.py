import copy
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import zlib
import unittest
from unittest import mock
from argparse import Namespace

HERE=Path(__file__).resolve().parents[1]/'integration'
spec=importlib.util.spec_from_file_location('maptest',HERE/'source_mapping.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
spec=importlib.util.spec_from_file_location('mapcontroltest',HERE/'source_mapping_control.py');control=importlib.util.module_from_spec(spec);spec.loader.exec_module(control)

def bits(v):return list(struct.unpack('<4I',struct.pack('<4f',*v)))
def mapping(w=17,h=33,flip=False):
    eyes=[]
    for eye in (0,1):
        eyes.append(dict(eye=eye,swapchain_id=10+eye,image_id=20+eye,image_view_id=30+eye,image_index=0,array_index=0,
            source_width=w,source_height=h,source_create_flags=0,source_vk_format=43,view_vk_format=43,
            source_array_size=1,source_face_count=1,source_sample_count=1,source_mip_count=1,source_image_count=2,
            view_format_provenance='pinned_comp_swapchain_view_creation',src_rect=[0,0,w,h],
            source_fov_bits_lrud=bits([-.5,.5,.5,-.5]),x_params=[w],y_params=[h],x_edges=list(range(w+1)),
            y_edges=list(range(h,-1,-1)) if flip else list(range(h+1))))
    return dict(type='mapping',sequence=1,compositor_id=1,frame=90,warp=7,semaphore_value=1,
        layer_target_host_ns=10000000,submit_return_host_ns=7000000,complete_host_ns=8000000,
        layer_flags=0,width=w,height=h,layer_count=1,layer_type=0,layer_index=0,flip_y=flip,
        direct_projection=True,view_space=False,producer_complete=True,eyes=eyes)
def trace(r):return [copy.deepcopy(m.HEADER),r,dict(type='footer',records=1,dropped=0,failed=0,complete=True)]
def view(r,eye):
    e=r['eyes'][eye];return dict(eye=eye,width=e['source_width'],height=e['source_height'],image_index=0,
        fov=dict(bits=list(e['source_fov_bits_lrud']),radians=[-.5,.5,.5,-.5]))
def event(name,**kwargs):
    r=dict.fromkeys(m.fresh.INTS,0);r.update(dict.fromkeys(m.fresh.BOOLS,False));r.update(type='event',event=name,app_frame_id=-1,system_frame_id=-1,when_ns=1000000,observed_ns=1000001);r.update(kwargs);return r
def sources():
    app=dict(pacer_id=2,app_frame_id=5)
    events=[event('app_predict',**app,display_time_ns=10000000,display_period_ns=11111111,wake_up_ns=2000000),
        event('app_delivered',**app,when_ns=3000000,observed_ns=3000001,display_time_ns=10000000),
        event('app_gpu_done',**app,when_ns=4000000,observed_ns=4000001),
        event('app_latched',**app,system_frame_id=90,when_ns=5000000,observed_ns=5000001),
        event('compositor_frame',system_frame_id=90,when_ns=6000000,observed_ns=6000000,display_time_ns=11000000,display_period_ns=11111111,layer_target_ns=10000000,layer_count=1,projection_count=1,direct_projection=True,projection_pose_reused=True),
        event('projection_layer',system_frame_id=90,when_ns=6000000,observed_ns=6000000,layer_target_ns=10000000)]
    for i,r in enumerate(events,1):r['sequence']=i
    return [dict(type='header',schema=1,clock='host_monotonic_ns',capacity=131072,diagnostic_only=True),*events,dict(type='footer',records=len(events),dropped=0,complete=True)]
def app_rows(r):return [dict(type='header',schema=3,source_reference_space='LOCAL',time_conversion_extension='XR_KHR_convert_timespec_time',time_conversion_function='xrConvertTimeToTimespecTimeKHR',converted_clock='CLOCK_MONOTONIC'),dict(type='frame',sequence=19,predicted_display_time=30000000,predicted_display_time_monotonic_ns=10000000,predicted_display_time_timespec=dict(tv_sec=0,tv_nsec=10000000),projection_submitted=True,views=[view(r,e)for e in(0,1)])]

class SourceMapping(unittest.TestCase):
    def test_exact_original_identity_and_y_flip(self):
        for flip in (False,True):
            r=mapping(flip=flip);m.records(trace(r))
            for eye in (0,1):
                raw=m.app.expected('rgb-ramp',17,33,eye)
                out=b''.join(raw[y*68:(y+1)*68]for y in range(32,-1,-1)) if flip else raw
                result=m.compare_eye(r,eye,raw,out,view(r,eye));self.assertTrue(result['protected_source_stage_exact'])
                self.assertEqual(result['one_cell_halo_nonunit_footprints'],0)
    def test_mutations_fail_closed_even_without_hash_dependency(self):
        mutations=[lambda r:r['eyes'].pop(),lambda r:r['eyes'][1].update(eye=0),lambda r:r.update(frame=91),
            lambda r:r.update(producer_complete=1),lambda r:r.update(view_space=True),lambda r:r.update(layer_flags=8),
            lambda r:r.update(complete_host_ns=6),lambda r:r.update(width=4097),lambda r:r.update(flip_y=True),
            lambda r:r['eyes'][0]['x_edges'].__setitem__(4,3),lambda r:r['eyes'][0]['x_params'].__setitem__(0,16),
            lambda r:r['eyes'][0].update(src_rect=[0,0,-17,33]),lambda r:r['eyes'][0].update(source_vk_format=37),
            lambda r:r['eyes'][0].update(source_sample_count=2),lambda r:r['eyes'][0].update(source_image_count=True),
            lambda r:r['eyes'][0].update(image_index=2),lambda r:r['eyes'][0].update(array_index=1),
            lambda r:r['eyes'][0]['source_fov_bits_lrud'].__setitem__(0,0x7fc00000),
            lambda r:r['eyes'][1].update(swapchain_id=10),lambda r:r['eyes'][1].update(image_id=20)]
        for mutate in mutations:
            r=mapping();mutate(r)
            with self.subTest(mutate=mutate):
                with self.assertRaises(ValueError):m.records(trace(r))
        for change in (dict(dropped=1),dict(failed=1),dict(complete=False),dict(records=2)):
            t=trace(mapping());t[-1].update(change)
            with self.assertRaises(ValueError):m.records(t)
    def test_cached_snapshot_and_identity_splice(self):
        r=mapping();r2=copy.deepcopy(r);r2.update(sequence=2,frame=180,semaphore_value=2,submit_return_host_ns=9000000,complete_host_ns=10000000)
        t=[dict(m.HEADER),r,r2,dict(type='footer',records=2,dropped=0,failed=0,complete=True)]
        self.assertEqual(len(m.records(t)),2) # byte-identical UBO on cache hit remains valid
        for key,value in [('swapchain_id',99),('image_id',99),('image_view_id',99)]:
            bad=copy.deepcopy(t);bad[2]['eyes'][0][key]=value
            with self.assertRaises(ValueError):m.records(bad)
    def test_nonunit_constant_source_cannot_false_qualify(self):
        r=mapping(17,17)
        for e in r['eyes']:
            e.update(source_width=19,src_rect=[0,0,19,17],x_params=[1,15,1],x_edges=[0]+list(range(2,18))+[19])
        m.records(trace(r));v=view(r,0)
        # Put compression at center to test mask refusal (legal ratio sequence
        # [8,1,8] has center one output texel; all other central samples average).
        for e in r['eyes']:
            e.update(source_width=33,src_rect=[0,0,33,17],x_params=[8,1,8],x_edges=list(range(0,17,2))+[17]+list(range(19,34,2)))
        m.records(trace(r));v=view(r,0)
        result=m.compare_eye(r,0,bytes([33,44,55,255])*33*17,bytes([33,44,55,255])*17*17,v)
        self.assertFalse(result['protected_source_stage_exact']);self.assertGreater(result['protected_texels_with_averaged_footprint'],0)
        self.assertEqual(result['protected_one_to_one_rgb_mismatched_pixels'],0)
    def test_one_code_and_alpha_errors_separate(self):
        r=mapping();raw=m.app.expected('rgb-ramp',17,33,0)
        for channel in(0,3):
            bad=bytearray(raw);bad[(16*17+8)*4+channel]^=1
            out=m.compare_eye(r,0,raw,bytes(bad),view(r,0));self.assertFalse(out['protected_source_stage_exact'])
            self.assertEqual(out['protected_one_to_one_rgb_mismatched_pixels'],1 if channel==0 else 0)
            self.assertEqual(out['protected_one_to_one_alpha_mismatched_pixels'],1 if channel==3 else 0)
    def test_target_join_is_runtime_conversion_not_app_sequence(self):
        r=mapping();joined,report=m.join([r],app_rows(r),sources())
        self.assertEqual(joined[90]['app_frame_id'],5);self.assertEqual(joined[90]['application']['sequence'],19)
        for mutate in (lambda a:a[1].update(predicted_display_time_monotonic_ns=30000000),lambda a:a[0].update(schema=2),lambda a:a[0].update(source_reference_space='VIEW')):
            a=app_rows(r);mutate(a)
            with self.assertRaises(ValueError):m.join([r],a,sources())
        for change in (dict(layer_type=1),dict(layer_flags=1),dict(submit_return_host_ns=5)):
            badr=mapping();badr.update(change)
            with self.assertRaises(ValueError):m.join([badr],app_rows(badr),sources())
        bad=sources();bad[2]['display_time_ns']+=1
        with self.assertRaises(ValueError):m.join([r],app_rows(r),bad)
    def test_exact_view_splices(self):
        r=mapping();raw=m.app.expected('rgb-ramp',17,33,0)
        for mutate in (lambda v:v.update(image_index=1),lambda v:v.update(width=16),lambda v:v['fov']['bits'].__setitem__(0,0)):
            v=view(r,0);mutate(v)
            with self.assertRaises(ValueError):m.compare_eye(r,0,raw,raw,v)

class Control(unittest.TestCase):
    def test_off_scrubs_and_rejects_receipts(self):
        a=Namespace(source_mapping=False);env={'WIVRN_CODEC_SOURCE_MAPPING_TRACE':'inherited'}
        self.assertEqual(control.configure(a,env,'/tmp/no-new-file'),{});self.assertEqual(env,{})
        a.source_mapping_build_receipt='/tmp/x'
        with self.assertRaises(ValueError):control.validate(a)
    def test_preflight_receipt_pins_native_sources_and_namespace(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            build=dict(sha256={n:m.capture.digest((HERE/'source-overlay'/n).read_bytes())for n in m.SOURCES},foveation_shaders={'foveation_rgb.comp':{}},monado_source_view_contract={})
            appbuild=dict(source_sha256={n:m.capture.digest((m.ROOT/'prototypes/vr444/experiments/quality_stimuli'/n).read_bytes())for n in ('app.cpp','pattern.hpp')},binary_sha256={'quality-stimuli':'e'*64})
            plan=dict(schema=2,source_reference_space='LOCAL',executable_sha256='e'*64)
            for name,data in [('build',build),('appbuild',appbuild),('plan',plan)]:(root/name).write_text(json.dumps(data))
            a=Namespace(source_mapping=True,mode='custom-rrb32',rrb_source='q1',q=1,compression='none',source_trace=True,quality_capture=True,
                source_mapping_build_receipt=root/'build',source_mapping_build_receipt_sha256=m.capture.digest((root/'build').read_bytes()),
                source_mapping_app_build_receipt=root/'appbuild',source_mapping_app_build_receipt_sha256=m.capture.digest((root/'appbuild').read_bytes()),
                quality_application_plan=root/'plan',quality_application_plan_sha256=m.capture.digest((root/'plan').read_bytes()))
            # Real native CLI has no frame_stride attribute. Fixed native1 is
            # established by emitted env and checked acquisition provenance.
            self.assertEqual(control.validate(a,HERE/'source-overlay'),m.SOURCES)
            env={'WIVRN_CODEC_SOURCE_MAPPING_TRACE':'inherited'};meta=control.configure(a,env,root/'out')
            self.assertEqual(env['WIVRN_CODEC_TEST_FRAME_STRIDE'],'1');self.assertEqual(env['WIVRN_CODEC_TEST_QUALITY_CAPTURE'],'1')
            self.assertEqual(meta['source_mapping_build_receipt_sha256'],a.source_mapping_build_receipt_sha256)
            for key,value in [('q',True),('q',8),('rrb_source','vr444'),('source_trace',False),('quality_capture',False),('compression','lz4'),('mode','custom-vr444'),('source_mapping',1),('source_mapping_build_receipt_sha256','a'*64),('source_mapping_app_build_receipt',None)]:
                bad=copy.copy(a);setattr(bad,key,value)
                with self.assertRaises(ValueError):control.validate(bad)
            build['sha256'][m.SOURCES[0]]='a'*64;(root/'build').write_text(json.dumps(build));a.source_mapping_build_receipt_sha256=m.capture.digest((root/'build').read_bytes())
            with self.assertRaises(ValueError):control.validate(a)

    def test_actual_provenance_names_coverage_and_clock(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'host').mkdir();(root/'app-source').mkdir();r=mapping()
            def write(n,x,lines=False):(root/n).write_text(''.join(json.dumps(v)+'\n'for v in x)if lines else json.dumps(x))
            hashes={name:'a'*64 for name in m.SOURCES}
            binary={key:'b'*64 for key in m.capture.BINARIES}
            prov=dict(source_mapping=True,custom_q=1,custom_compression='none',custom_frame_stride=1,rrb_source='q1',source_helper_sha256=hashes,**binary)
            write('provenance.json',prov);write(m.TRACE,trace(r),True);write('app-source/app.jsonl',app_rows(r),True);write('host/source-trace.jsonl',sources(),True)
            for eye in(0,1):write(f'host/encode-{eye}.jsonl',[dict(frame=90,eye=eye)],True)
            build=root/'receipt';build.write_text(json.dumps(dict(sha256=hashes|{name:'b'*64 for name in m.capture.BINARIES.values()})))
            args=Namespace(source_mapping=True,source_mapping_build_receipt=build)
            with mock.patch.object(control,'validate',return_value=m.SOURCES):
                self.assertEqual(control.evaluate(args,root)['status'],'pass')
                write('host/encode-1.jsonl',[],True);self.assertEqual(control.evaluate(args,root)['status'],'fail')
                write('host/encode-1.jsonl',[dict(frame=90,eye=1)],True)
                rows=app_rows(r);rows[1]['predicted_display_time_timespec']['tv_nsec']+=1;write('app-source/app.jsonl',rows,True)
                self.assertEqual(control.evaluate(args,root)['status'],'fail')
                write('app-source/app.jsonl',app_rows(r),True);prov['q']=prov.pop('custom_q');write('provenance.json',prov)
                self.assertEqual(control.evaluate(args,root)['status'],'fail')

class FormalAdapter(unittest.TestCase):
    def test_complete_contract_snapshot_and_rehashed_mutations(self):
        # Existing submitted-image auditor is a separately tested upstream gate;
        # here its context is supplied to exercise every NEW formal adapter join.
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);runroot=root/'run';buildroot=root/'native';runroot.mkdir();buildroot.mkdir();(runroot/'host').mkdir();appdir=runroot/'app-source';appdir.mkdir()
            sys.path.insert(0,str(m.ROOT/'prototypes/vr444/experiments/quality_stimuli'))
            try:
                fixture=m.module('map_app_fixture',m.ROOT/'prototypes/vr444/experiments/quality_stimuli/test_audit.py').fixture
                plan_module=m.module('map_plan_fixture',m.ROOT/'prototypes/vr444/experiments/quality_stimuli/plan.py')
            finally:sys.path.pop(0)
            rows=fixture(appdir);rows[0].update(schema=3,space='LOCAL',source_reference_space='LOCAL',locate_and_layer_use_same_space=True,
                time_conversion_extension='XR_KHR_convert_timespec_time',time_conversion_function='xrConvertTimeToTimespecTimeKHR',converted_clock='CLOCK_MONOTONIC')
            rows[-2].update(source_reference_space='LOCAL',predicted_display_time_monotonic_ns=10000000,predicted_display_time_timespec=dict(tv_sec=0,tv_nsec=10000000))
            r=mapping()
            def put(name,data):
                path=runroot/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
            def js(name,value):put(name,json.dumps(value).encode())
            def jl(name,value):put(name,b''.join(json.dumps(v).encode()+b'\n'for v in value))
            jl('app-source/app.jsonl',rows);jl(m.TRACE,trace(r));jl('host/source-trace.jsonl',sources())
            for eye in(0,1):
                put(f'host/90-{eye}.rgba',(appdir/f'eye{eye}.source.rgba').read_bytes())
                body=b'private-opaque-format-test-body';wire=struct.pack('<6I2Q2I',0x31544357,4,17,33,eye,len(body),90,7,zlib.crc32(body),0)+body
                put(f'host/90-{eye}.envelope',wire)
                jl(f'host/encode-{eye}.jsonl',[dict(frame=90,eye=eye,warp=7,payload_bytes=len(wire),effective_codec='rrb32',quantizer=1,source_contract='post_foveation_rgb_exact')])
            for name in m.SOURCES:
                p=buildroot/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((HERE/'source-overlay'/name).read_bytes())
            spv=struct.pack('<5I',0x07230203,0x10000,0,1,0);(buildroot/'s.spv').write_bytes(spv);(buildroot/'s.literal').write_text(''.join(f'0x{x:08x},'for x in struct.unpack('<5I',spv)))
            bh={str(p.relative_to(buildroot)):m.capture.digest(p.read_bytes())for p in buildroot.rglob('*')if p.is_file()}
            external=root/'external/src/xrt/compositor/util/comp_swapchain.c';external.parent.mkdir(parents=True);external.write_text('vk_format_convert_unorm_to_srgb XRT_SWAPCHAIN_CREATE_SAMPLE_AS_SRGB')
            esha=m.capture.digest(external.read_bytes())
            receipt=dict(sha256=bh,foveation_shaders={'foveation_rgb.comp':dict(path='s.spv',sha256=bh['s.spv'],embedded_path='s.literal',embedded_sha256=bh['s.literal'],literal_equivalent=True)},monado_source_view_contract=dict(source=str(root/'external'),files={'src/xrt/compositor/util/comp_swapchain.c':esha}),external_artifacts_sha256={str(external):esha})
            binary=root/'elf';binary.write_bytes(b'\x7fELFbounded-fixture');binary_sha=m.capture.digest(binary.read_bytes())
            plan=plan_module.prepare(binary,binary_sha,root/'bundle',runroot/'app-source-new','rgb-ramp',17,33,2,1)
            plan['capture_directory']=str(appdir);plan['application']=m.application_control.argv(plan,plan['executable'])
            planbytes=json.dumps(plan).encode();put('quality-application-plan.json',planbytes);plan_sha=m.capture.digest(planbytes)
            actual=m.application_control.argv(plan,'/private/snapshot')
            execution=dict(original_plan_sha256=plan_sha,executable_sha256=binary_sha,snapshot_unchanged=True,snapshot_sha256_after=binary_sha,snapshot_path='/private/snapshot',actual_application=actual,application=actual,source_application_space='LOCAL',latency_eligible=False,pattern='rgb-ramp',width=17,height=33)
            js('quality-application-execution.json',execution);js('server-config.json',dict(application=actual))
            prov=dict(mode='custom-rrb32',rrb_source='q1',custom_q=1,custom_compression='none',source_mapping=True,source_trace=True,quality_application=True,source_helper_sha256=bh,quality_application_plan_sha256=plan_sha,host_application_sha256=binary_sha)
            app_receipt=root/'app-build.json';app_receipt.write_text(json.dumps(dict(binary_sha256={'quality-stimuli':binary_sha},source_sha256={name:m.capture.digest((m.ROOT/'prototypes/vr444/experiments/quality_stimuli'/name).read_bytes())for name in('app.cpp','pattern.hpp')})))
            app_sha=m.capture.digest(app_receipt.read_bytes());prov.update(source_mapping_build_receipt_sha256='b'*64,source_mapping_app_build_receipt_sha256=app_sha)
            def invoke():
                hashes={str(p.relative_to(runroot)):m.capture.digest(p.read_bytes())for p in runroot.rglob('*')if p.is_file()}
                def upstream(*args,**kwargs):kwargs['_context'].update(run=m.capture.Bound(runroot,hashes),build=m.capture.Bound(buildroot,bh),receipt=receipt,provenance=prov);return {'status':'pass'},{}
                with mock.patch.object(m.capture,'audit',side_effect=upstream):return m.audit(runroot,'a'*64,'unused','b'*64,app_receipt,app_sha)
            result=invoke();self.assertTrue(result['protected_source_stage_exact']);self.assertFalse(result['full_goal_qualified']);self.assertEqual(result['sampled_encoded_pairs'],1)
            saved=prov['source_mapping_build_receipt_sha256'];prov['source_mapping_build_receipt_sha256']='c'*64
            with self.assertRaises(ValueError):invoke()
            prov['source_mapping_build_receipt_sha256']=saved
            # Full formal path cannot inherit a legacy q/compression spelling.
            prov['q']=prov.pop('custom_q')
            with self.assertRaises(ValueError):invoke()
            prov['custom_q']=prov.pop('q')
            # Every mutation is reflected in the new immutable manifest, so
            # these reject semantic/identity defects instead of only old hashes.
            original=(runroot/m.TRACE).read_bytes()
            for mutate in (lambda t:t[1].update(frame=180),lambda t:t[1].update(warp=8),lambda t:t[1].update(layer_target_host_ns=10000001),lambda t:t[1]['eyes'][0].update(image_index=1),lambda t:t[1]['eyes'][0]['source_fov_bits_lrud'].__setitem__(0,0xbf000001)):
                bad=trace(mapping());mutate(bad);jl(m.TRACE,bad)
                with self.assertRaises(ValueError):invoke()
            put(m.TRACE,original)
            right=(runroot/'host/encode-1.jsonl').read_bytes();jl('host/encode-1.jsonl',[])
            with self.assertRaises(ValueError):invoke()
            put('host/encode-1.jsonl',right)
            raw=bytearray((runroot/'host/90-0.rgba').read_bytes());raw[(16*17+8)*4]^=1;put('host/90-0.rgba',bytes(raw))
            self.assertFalse(invoke()['protected_source_stage_exact'])

if __name__=='__main__':unittest.main()
