import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

HERE=Path(__file__).parents[1]/'integration'
sys.path.insert(0,str(HERE))
import submitted_comparison as comparison
q=comparison.quality


def sha(data):return hashlib.sha256(data).hexdigest()
def save(path,obj):path.write_text(json.dumps(obj));return sha(path.read_bytes())
def put(root,name,data):
    p=root/name;p.write_bytes(data);return dict(path=name,sha256=sha(data),bytes=len(data))


class Comparison(unittest.TestCase):
    def fixture(self,root,fmt=43):
        w,h=17,19;capture_id,frame,warp=1,30,777
        captures=[];sources=[];run_inputs={};products=[]
        for eye in (0,1):
            projection=dict(schema=1,geometry=[w,h],frame=frame,warp=warp,eye=eye,kind='unwarped-perspective',
                source_boundary='submitted-image',mapping_source='actual submitted XR projection viewport/FOV',
                gaze_mode='fixed-center',protected_radius_degrees=10,fov_radians=[-.5,.5,-.5,.5])
            x,y=q.perspective_edges(w,h,projection['fov_radians']);mask=q.protected_mask(w,h,x,y)
            rgba=bytes(v for i in range(w*h) for v in (i%256,eye*113,(i*7)%256,255))
            candidate=put(root,f'candidate-{eye}.rgba',rgba)
            projection_desc=put(root,f'projection-{eye}.json',json.dumps(projection).encode())
            mask_desc=put(root,f'mask-{eye}.u8',mask.tobytes())
            captures.append(dict(capture_id=1,frame=frame,warp=warp,eye=eye,geometry=[w,h],source_boundary='submitted-image',
                channel_format='RGBA8',permutation_only=True,pixel_values_color_converted=False,
                channel_permutation=[0,1,2,3] if fmt==43 else [2,1,0,3],candidate=candidate,
                projection=projection_desc,protected_mask=mask_desc,protected_pixels=int(mask.sum())))
            raw=rgba if fmt==43 else bytes(v for i in range(0,len(rgba),4) for v in (rgba[i+2],rgba[i+1],rgba[i],rgba[i+3]))
            products.append(put(root,f'eye-{eye}.raw',raw)|dict(channel_order='RGBA' if fmt==43 else 'BGRA'))
            source=dict(frame=frame,warp=warp,eye=eye,source_boundary='post-host-foveation-codec-input',
                encoded_payload_pixels_used_as_reference=False,decoder_output_used_as_reference=False,original_application_rgb_claim=False)
            for key in ('source','envelope','encode_event_input'):
                source[key]=dict(path=f'{key}-{eye}',sha256=sha((key+str(eye)).encode()));run_inputs[source[key]['path']]={'sha256':source[key]['sha256']}
            sources.append(source)
        shaders={};files=[]
        for name in ('vert','frag'):
            data=(name+' shader').encode();p=root/f'reprojection.{name}.spv';p.write_bytes(data)
            shaders[f'reprojection.{name}']=dict(sha256=sha(data),literal_equivalent=True,literal_equivalence_recomputed=True)
            files.append(dict(local=str(p),remote=p.name,sha256=sha(data)))
        native=dict(schema=1,status='pass',captured_pairs=1,captures=captures,run_inputs=run_inputs,build_inputs={'original':{'sha256':'d'*64}},
                    headset_serial_recorded='physical-serial',run_hashes_sha256='a'*64,build_receipt_sha256='b'*64,renderer_shaders=shaders)
        job_data=struct.pack('<6I',0x31504c52,1,w,h,fmt,2)+b'upstream-audited-body'
        desc=put(root,'job.rlp',job_data)|dict(format='RLP1',version=1)
        output=dict(width=w,height=h,vk_format=fmt)
        job=dict(schema=1,status='pass',capture_id=capture_id,frame=frame,warp=warp,candidate_capture_audit=copy.deepcopy(native),
            run_hashes_sha256='a'*64,build_receipt_sha256='b'*64,renderer_shaders=shaders,output=output,job=desc,independent_inputs=sources,run_inputs=run_inputs,build_inputs=copy.deepcopy(native['build_inputs']),
            source_boundary='post-host-foveation-codec-input',geometry_reconstructed_from_metadata=True,
            actual_native_vertex_bytes_captured=False,actual_native_push_constant_bytes_captured=False,
            aarch64_raster_geometry_bit_equivalence_verified=False,full_goal_qualified=False)
        native_sha=save(root/'native.json',native);job_sha=save(root/'job.json',job)
        files += [dict(local=str(root/'job.rlp'),remote='job.rlp',sha256=sha(job_data)),dict(local=str(root/'replay'),remote='replay',sha256='c'*64)]
        plan=dict(expected_serial='physical-serial',files=files,replay=dict(source_adapter_sha256=job_sha,native_build_receipt_sha256='b'*64,output=output,job_sha256=sha(job_data)))
        plan_sha=save(root/'plan.json',plan)
        replay=dict(schema=1,status='pass',plan_sha256=plan_sha,source_adapter_sha256=job_sha,native_build_receipt_sha256='b'*64,output=output,
            actual_headset_used=True,omission_control_verified=True,raw_format_preserved=True,headset_physical_serial='physical-serial',
            native_recorded_identity='physical-serial',native_identity_method='direct_recorded_physical_serial',eyes=products)
        replay_sha=save(root/'replay.json',replay)
        return native,job,replay,plan

    def run_compare(self,root):
        return comparison.compare(root/'native.json',sha((root/'native.json').read_bytes()),root/'job.json',sha((root/'job.json').read_bytes()),
            root/'replay.json',sha((root/'replay.json').read_bytes()),root/'plan.json',root)

    def rebind(self,root,native,job,replay,plan):
        job['candidate_capture_audit']=copy.deepcopy(native)
        save(root/'native.json',native);job_sha=save(root/'job.json',job)
        plan['replay']['source_adapter_sha256']=job_sha
        replay['source_adapter_sha256']=job_sha
        replay['plan_sha256']=save(root/'plan.json',plan);save(root/'replay.json',replay)

    def test_repaired_control_hashes_still_require_semantic_contracts(self):
        mutations=[lambda n,j,r,p:j['independent_inputs'][1].update(eye=0),
          lambda n,j,r,p:j['independent_inputs'][1].update(warp=778),
          lambda n,j,r,p:j['independent_inputs'][1].update(decoder_output_used_as_reference=True),
          lambda n,j,r,p:n['captures'][1].update(frame=31),
          lambda n,j,r,p:n['captures'][1].update(eye=True),
          lambda n,j,r,p:n['captures'][1].update(channel_permutation=[2,1,0,3]),
          lambda n,j,r,p:r.update(native_identity_method='assumed_address'),
          lambda n,j,r,p:p['files'][0].update(sha256='d'*64)]
        for mutate in mutations:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);values=self.fixture(root);mutate(*values);self.rebind(root,*values)
                with self.assertRaises((ValueError,KeyError,TypeError)):self.run_compare(root)

    def test_repaired_projection_and_mask_hashes_do_not_change_geometry(self):
        for target in ('projection','protected_mask'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);native,job,replay,plan=self.fixture(root);desc=native['captures'][1][target];p=root/desc['path']
                if target=='projection':
                    value=json.loads(p.read_bytes());value['eye']=0;p.write_text(json.dumps(value))
                else:
                    value=bytearray(p.read_bytes());value[0]^=1;p.write_bytes(value)
                desc.update(sha256=sha(p.read_bytes()),bytes=p.stat().st_size)
                self.rebind(root,native,job,replay,plan)
                with self.assertRaises(ValueError):self.run_compare(root)

    def test_actual_collector_absolute_paths_and_job_build_superset(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);native,job,replay,plan=self.fixture(root)
            for eye in replay['eyes']:eye['path']=str(root/eye['path'])
            for name in ('server/encoder/video_encoder_test.cpp','common/codec_test/wire.h'):
                job['build_inputs'][name]={'sha256':'e'*64}
                job['candidate_capture_audit']['build_inputs'][name]={'sha256':'e'*64}
            job_sha=save(root/'job.json',job);plan['replay']['source_adapter_sha256']=job_sha;replay['source_adapter_sha256']=job_sha
            replay['plan_sha256']=save(root/'plan.json',plan);save(root/'replay.json',replay)
            self.assertEqual(self.run_compare(root)['status'],'pass')
            replay['eyes'][1]['path']=str(root.parent/'eye-1.raw');save(root/'replay.json',replay)
            with self.assertRaises(ValueError):self.run_compare(root)

    def test_extra_job_build_inputs_cannot_change_original_or_add_arbitrary_code(self):
        for name in ('original','unknown.cpp'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);native,job,replay,plan=self.fixture(root)
                job['build_inputs'][name]={'sha256':'e'*64};job['candidate_capture_audit']['build_inputs'][name]={'sha256':'e'*64}
                job_sha=save(root/'job.json',job);plan['replay']['source_adapter_sha256']=job_sha;replay['source_adapter_sha256']=job_sha
                replay['plan_sha256']=save(root/'plan.json',plan);save(root/'replay.json',replay)
                with self.assertRaises(ValueError):self.run_compare(root)

    def test_exact_rgba_bgra_and_scope(self):
        for fmt in (43,50):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);self.fixture(root,fmt);r=self.run_compare(root)
                self.assertEqual(r['status'],'pass');self.assertEqual(len(r['eyes']),2)
                self.assertTrue(all(e['full_rgb_exact'] and e['full_alpha_exact'] for e in r['eyes']))
                self.assertFalse(r['full_goal_qualified']);self.assertFalse(r['original_application_rgb_verified']);self.assertFalse(r['displayed_pixels_verified'])

    def test_bound_control_and_identity_mutants(self):
        changes=[('native',lambda v:v['captures'].pop()),('job',lambda v:v.update(frame=31)),
          ('job',lambda v:v['independent_inputs'][1].update(eye=0)),('job',lambda v:v['independent_inputs'][1].update(decoder_output_used_as_reference=True)),
          ('replay',lambda v:v['eyes'].pop()),('replay',lambda v:v.update(native_build_receipt_sha256='d'*64)),
          ('replay',lambda v:v.update(headset_physical_serial='different')),('replay',lambda v:v.update(omission_control_verified=False)),
          ('replay',lambda v:v.update(source_adapter_sha256='d'*64)),('replay',lambda v:v.update(native_recorded_identity='different')),
          ('replay',lambda v:v['eyes'][1].update(channel_order='BGRA')),('replay',lambda v:v['eyes'][1].update(path=v['eyes'][0]['path'])),
          ('plan',lambda v:v['files'][0].update(sha256='d'*64))]
        for name,change in changes:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);values=dict(zip(('native','job','replay','plan'),self.fixture(root)));change(values[name]);save(root/(name+'.json'),values[name])
                with self.assertRaises((ValueError,KeyError,TypeError),msg=name):self.run_compare(root)

    def test_unbound_bytes_masks_and_symlink_rejected(self):
        for name in ('candidate-1.rgba','eye-1.raw','mask-1.u8','projection-1.json','job.rlp','reprojection.frag.spv'):
            with tempfile.TemporaryDirectory() as d:
                root=Path(d);self.fixture(root);p=root/name;data=bytearray(p.read_bytes());data[0]^=1;p.write_bytes(data)
                with self.assertRaises((ValueError,KeyError,TypeError),msg=name):self.run_compare(root)
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root);p=root/'eye-1.raw';data=p.read_bytes();p.unlink();(root/'other').write_bytes(data);p.symlink_to(root/'other')
            with self.assertRaises(ValueError):self.run_compare(root)

    def test_rehashed_one_code_rgb_error_fails_without_tolerance(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);native,job,replay,plan=self.fixture(root)
            mask=(root/'mask-1.u8').read_bytes();pixel=mask.index(1);p=root/'eye-1.raw';raw=bytearray(p.read_bytes());raw[4*pixel]^=1;p.write_bytes(raw)
            replay['eyes'][1]['sha256']=sha(bytes(raw));save(root/'replay.json',replay)
            r=self.run_compare(root);self.assertEqual(r['status'],'fail');self.assertEqual(r['eyes'][1]['protected_max_absolute_code_error'],1)

    def test_alpha_is_reported_separately(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);native,job,replay,plan=self.fixture(root);p=root/'eye-1.raw';raw=bytearray(p.read_bytes());raw[3]^=1;p.write_bytes(raw)
            replay['eyes'][1]['sha256']=sha(bytes(raw));save(root/'replay.json',replay)
            r=self.run_compare(root);self.assertEqual(r['status'],'pass');self.assertFalse(r['eyes'][1]['full_alpha_exact']);self.assertFalse(r['eyes'][1]['opaque_alpha_reference'])

    def test_explicit_receipt_hash_and_json_fail_closed(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.fixture(root)
            with self.assertRaises(ValueError):comparison.compare(root/'native.json','0'*64,root/'job.json','0'*64,root/'replay.json','0'*64,root/'plan.json',root)
            for raw in (b'{"schema":1,"schema":1}',b'{"value":NaN}'):
                with self.assertRaises(ValueError):comparison.strict(raw)

if __name__=='__main__':unittest.main()
