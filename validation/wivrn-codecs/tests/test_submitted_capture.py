import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest

SPEC=importlib.util.spec_from_file_location('submitted_capture',Path(__file__).parents[1]/'integration/submitted_capture.py')
audit=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(audit)


def encoded(value):return (json.dumps(value,sort_keys=True)+'\n').encode()


class CaptureAudit(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.run=self.root/'run';self.build=self.root/'build'
        self.run.mkdir();self.build.mkdir();self.receipt_path=self.root/'receipt.json'
        sha={}
        for name in set(audit.SOURCES)|{'client/decoder/test_decoder.cpp','client/application.cpp'}:
            data=('source '+name).encode();self.put(self.build,name,data);sha[name]=audit.digest(data)
        for name in audit.BINARIES.values():sha[name]=audit.digest(('binary '+name).encode())
        shaders={}
        for name in ['reprojection.vert','reprojection.frag']:
            path='shaders/'+name+'.spv-opt';literal='shaders/'+name+'.spv';raw=struct.pack('<5I',0x07230203,0x10000,0,5,0)
            text=b'0x7230203, 0x10000, 0x0, 0x5, 0x0,  '
            self.put(self.build,path,raw);self.put(self.build,literal,text);sha[path]=audit.digest(raw);sha[literal]=audit.digest(text)
            shaders[name]={'path':path,'sha256':sha[path],'embedded_path':literal,'embedded_sha256':sha[literal],'literal_equivalent':True}
        self.receipt={'schema':1,'source':str(self.build),'sha256':sha,'renderer_shaders':shaders};self.seal_receipt()
        config={'enabled':True,'max_pairs':2,'frame_stride':90}
        provenance={'schema':1,'quality_capture':True,'headset_serial':'recorded-fixture-device','mode':'custom-rrb32',
            'quality_max_pairs':2,'quality_frame_stride':90,'source_helper_sha256':{n:sha[n] for n in audit.SOURCES},
            'client_decoder_source_sha256':sha['client/decoder/test_decoder.cpp'],'application_source_sha256':sha['client/application.cpp'],
            'harness_sha256':'d'*64}
        provenance.update({key:sha[name] for key,name in audit.BINARIES.items()})
        self.putj('provenance.json',provenance);self.putj('cleanup.json',{'restored':True,'cleanup_errors':[],'app_pid_observed':True})
        self.putj('summary.json',{'status':'pass'});self.putj('client-config-readback.json',{'codec-test-quality-capture.json':json.dumps(config)})
        rows=[]
        for cid in (1,2):
            frame=cid*90;warp=cid+20;clock=cid*1000;stem=f'capture-{cid}'
            rows.append({'left':frame,'right':frame,'left_warp':warp,'right_warp':warp,'end_frame_return_xr_ns':clock,
                         'submit_xr_ns':clock+100,'predicted_display_xr_ns':clock+50})
            self.putj(audit.PREFIX+stem+'-pair.json',{'schema':1,'capture_id':cid,'complete_eyes':[0,1],
                'source_boundary':'submitted-image','quality_pass':False,'latency_eligible':False})
            for eye in (0,1):
                meta=self.meta(cid,eye,frame,warp,clock)
                self.putj(audit.PREFIX+stem+f'-eye-{eye}.json',meta)
                self.put(self.run,audit.PREFIX+stem+f'-eye-{eye}.raw',bytes([11+eye,22,33,255])*16)
        self.put(self.run,'client/codec-test-present.jsonl',b''.join(encoded(row) for row in rows));self.rehash()
    def put(self,root,name,data):
        path=root/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    def putj(self,name,value):self.put(self.run,name,encoded(value))
    def getj(self,name):return json.loads((self.run/name).read_bytes())
    def rehash(self):
        hashes={str(p.relative_to(self.run)):audit.digest(p.read_bytes()) for p in self.run.rglob('*') if p.is_file() and p.name!='hashes.json'}
        self.putj('hashes.json',hashes)
    def seal_receipt(self):
        self.receipt_path.write_bytes(encoded(self.receipt));self.receipt_sha=audit.digest(self.receipt_path.read_bytes())
    def evaluate(self):return audit.audit(self.run,self.receipt_path,self.receipt_sha)
    @staticmethod
    def meta(cid,eye,frame,warp,clock):
        fov=[-.8,.8,-.8,.8];pose={'position':[0,0,0],'orientation_xyzw':[0,0,0,1]}
        return {'schema':1,'capture_id':cid,'eye':eye,'array_layer':eye,'frame':frame,'warp':warp,'decoder_generation_first_frame':0,
            'swapchain_image_index':0,'source_boundary':'submitted-image','successful_submit_xr_ns':clock,'predicted_display_xr_ns':clock+50,
            'raw_file':f'capture-{cid}-eye-{eye}.raw','width':4,'height':4,'row_bytes':16,'bytes':64,'row_order':'top-to-bottom',
            'vk_format':43,'channel_order':'RGBA','canonical_rgba':True,'subimage_rect':[0,0,4,4],
            'unwarped_fov':fov,'render_fov_left_right_down_up_radians':fov,'encoded_input_fov_left_right_down_up_radians':fov,
            'render_pose':pose,'encoded_input_pose':pose,'rect_rgb':[0,0,4,4],'rect_a':[0,0,0,0],'encoded_input_extent':[4,4],
            'foveation_x':[4],'foveation_y':[4],'scale_rgba':[1,1,1,1],'bias_rgba':[0,0,0,0],'cutout_mask':[[0]*4 for _ in range(3)],
            'output_alpha':False,'sampled_rgb_is_linear':True,'sampler_contract_source_bound':True,'sampler_runtime_queried':False,
            'alpha_image_bound':False,'alpha_sampler_bound':False,'shader_alpha_specialization':0,'hmd_needs_srgb_conversion':True,
            'shader_srgb_decode':False,'input_view_format_source_bound':True,'input_view_vk_format':43,'input_image_layout':5,
            'filter_extension_enabled':False,'shader_bytes_bound_by_collector':False,'requested_filter_flags':0,'submitted_filter_flags':0,
            'shader_load_names':['reprojection.vert','reprojection.frag'],
            'sampler_contract':{'origin':'test_decoder::rgb_sampler','min':'LINEAR','mag':'LINEAR','mipmap':'NEAREST','address_uvw':'CLAMP_TO_EDGE',
                'normalized_coordinates':True,'min_lod':0,'max_lod':0,'lod_bias':0,'anisotropy':False,'max_anisotropy':0,'compare':False,'compare_op':'NEVER','border_color':'FLOAT_TRANSPARENT_BLACK'},
            **{key:True for key in ['readback_fence_observed','projection_submitted','transfer_src_usage','srgb_encoded_rgb','alpha_unorm']},
            **{key:False for key in ['quality_pass','latency_eligible','displayed_pixels_verified','independent_reference','runtime_filter_effect_verified']}}
    def test_complete_bound_capture_and_explicit_nonqualification(self):
        report,products=self.evaluate();self.assertEqual(report['captured_pairs'],2);self.assertEqual(len(report['captures']),4)
        self.assertFalse(report['quality_pass']);self.assertFalse(report['full_goal_qualified']);self.assertFalse(report['independent_reference_present'])
        self.assertEqual(products['capture-1-eye-0.rgba'],bytes([11,22,33,255])*16)
        self.assertGreater(report['captures'][0]['protected_pixels'],0)
        self.assertTrue(report['renderer_shaders']['reprojection.vert']['literal_equivalence_recomputed'])
    def test_bgra_permutation_retains_exact_values_and_hashes(self):
        for cid in (1,2):
            for eye in (0,1):
                path=audit.PREFIX+f'capture-{cid}-eye-{eye}.json';meta=self.getj(path)
                meta.update(vk_format=50,channel_order='BGRA',canonical_rgba=False);self.putj(path,meta)
        self.rehash();report,products=self.evaluate();self.assertEqual(products['capture-1-eye-0.rgba'],bytes([33,22,11,255])*16)
        self.assertEqual(report['captures'][0]['channel_permutation'],[2,1,0,3])
        self.assertNotEqual(report['captures'][0]['raw_input']['sha256'],report['captures'][0]['candidate']['sha256'])
    def test_semantic_mutations_reject_even_after_rehash(self):
        name=audit.PREFIX+'capture-1-eye-1.json';original=self.getj(name)
        mutations=[('frame',91),('warp',999),('eye',True),('capture_id',True),('array_layer',True),('frame',True),('warp',True),
            ('schema',True),('width',True),('row_bytes',True),('vk_format',True),('vk_format',50),('channel_order','BGRA'),
            ('source_boundary','decoded-source'),('quality_pass',True),('successful_submit_xr_ns',1100),
            ('successful_submit_xr_ns',True),('readback_fence_observed',1),('bytes',63),('raw_file','capture-1-eye-0.raw'),
            ('row_order','bottom-to-top'),('unwarped_fov',[-.1,.1,-.1,.1]),('unwarped_fov',[False,.8,-.8,.8]),
            ('unwarped_fov',[float('nan'),.8,-.8,.8]),('foveation_x',[2]),('shader_srgb_decode',True),
            ('sampler_contract_source_bound',False),('input_view_vk_format',50),('alpha_image_bound',True)]
        for key,value in mutations:
            with self.subTest(key=key,value=value):
                meta=copy.deepcopy(original);meta[key]=value;self.putj(name,meta);self.rehash()
                with self.assertRaises(ValueError):self.evaluate()
        self.putj(name,original);self.rehash();self.evaluate()
    def test_missing_final_marker_and_partial_pair_reject(self):
        for suffix in ('pair.json','eye-1.raw'):
            name=audit.PREFIX+'capture-2-'+suffix;data=(self.run/name).read_bytes();(self.run/name).unlink();self.rehash()
            with self.assertRaises(ValueError):self.evaluate()
            self.put(self.run,name,data);self.rehash()
    def test_presentation_exact_join_and_typed_ids(self):
        name='client/codec-test-present.jsonl';original=(self.run/name).read_bytes();rows=[json.loads(x) for x in original.splitlines()]
        for key,value in [('end_frame_return_xr_ns',1001),('right',91),('right_warp',22),('left',True),('submit_xr_ns',999)]:
            bad=copy.deepcopy(rows);bad[0][key]=value;self.put(self.run,name,b''.join(encoded(x) for x in bad));self.rehash()
            with self.assertRaises(ValueError):self.evaluate()
        self.put(self.run,name,original+encoded(rows[0]));self.rehash()
        with self.assertRaises(ValueError):self.evaluate()
    def test_hash_drift_and_pinned_receipt(self):
        path=self.run/(audit.PREFIX+'capture-1-eye-0.raw');path.write_bytes(bytes(64))
        with self.assertRaises(ValueError):self.evaluate()
        with self.assertRaises(ValueError):audit.audit(self.run,self.receipt_path,'0'*64)
    def test_source_shader_and_literal_bindings(self):
        name=audit.SOURCES[0];path=self.build/name;old=path.read_bytes();path.write_bytes(b'changed')
        with self.assertRaises(ValueError):self.evaluate()
        path.write_bytes(old)
        item=self.receipt['renderer_shaders']['reprojection.vert'];path=self.build/item['embedded_path'];old=path.read_bytes()
        path.write_bytes(old+b'garbage');self.receipt['sha256'][item['embedded_path']]=audit.digest(path.read_bytes());item['embedded_sha256']=audit.digest(path.read_bytes());self.seal_receipt()
        with self.assertRaises(ValueError):self.evaluate()
    def test_duplicate_json_and_extra_unbound_artifact(self):
        name=audit.PREFIX+'capture-1-pair.json';data=(self.run/name).read_bytes();self.put(self.run,name,data.replace(b'"schema": 1',b'"schema": 1,"schema":1'));self.rehash()
        with self.assertRaises(ValueError):self.evaluate()
        self.put(self.run,name,data);self.rehash();self.put(self.run,audit.PREFIX+'unbound.raw',b'extra')
        with self.assertRaises(ValueError):self.evaluate()
    def test_config_bool_and_cleanup_failure(self):
        original=self.getj('client-config-readback.json');bad=copy.deepcopy(original);config=json.loads(bad['codec-test-quality-capture.json']);config['max_pairs']=True
        bad['codec-test-quality-capture.json']=json.dumps(config);self.putj('client-config-readback.json',bad);self.rehash()
        with self.assertRaises(ValueError):self.evaluate()
        self.putj('client-config-readback.json',original);self.putj('cleanup.json',{'restored':False,'cleanup_errors':['failure'],'app_pid_observed':True});self.rehash()
        with self.assertRaises(ValueError):self.evaluate()
    def test_bound_buffers_and_no_overwrite_output(self):
        data=b'consumed';self.put(self.root,'buffer',data);bound=audit.Bound(self.root,{'buffer':audit.digest(data)})
        self.assertEqual(bound.read('buffer'),data);self.put(self.root,'buffer',b'changed-after-read');self.assertEqual(bound.read('buffer'),data)
        report,products=self.evaluate();output=self.root/'audited';audit.write_output(output,report,products)
        with self.assertRaises(FileExistsError):audit.write_output(output,report,products)
    def test_symlink_artifacts_reject(self):
        name=audit.PREFIX+'capture-1-eye-0.raw';data=(self.run/name).read_bytes();(self.run/name).unlink();self.put(self.root,'external.raw',data)
        (self.run/name).symlink_to(self.root/'external.raw')
        with self.assertRaises(ValueError):self.evaluate()

if __name__=='__main__':unittest.main()
