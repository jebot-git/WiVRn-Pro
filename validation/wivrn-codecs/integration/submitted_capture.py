#!/usr/bin/env python3
"""Audit bound native submitted images; emit candidates, never a quality pass."""
import argparse
import hashlib
import importlib.util
import json
import math
from pathlib import Path, PurePosixPath
import re
import struct

import numpy as np

HERE = Path(__file__).resolve().parent
SPEC = importlib.util.spec_from_file_location('submitted_capture_quality', HERE/'research_quality.py')
quality = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(quality)
MAX_RAW = 4*quality.MAX_PIXELS
PREFIX = 'client/codec-test/quality/'
SOURCES = ('common/codec_test/submitted_capture.h', 'client/scenes/stream_quality_capture.h',
           'client/scenes/stream.cpp', 'client/scenes/stream_defoveator.cpp',
           'client/xr/swapchain.cpp', 'client/xr/swapchain.h')
BINARIES = {'server_sha256':'build/codec-server/server/wivrn-pro-server',
            'installed_apk_sha256':'build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk',
            'runtime_manifest_sha256':'build/codec-server/openxr_wivrn-dev.json'}


def require(condition, message):
    if not condition: raise ValueError(message)


def digest(data): return hashlib.sha256(data).hexdigest()
def valid_sha(value): return type(value) is str and re.fullmatch('[0-9a-f]{64}',value) is not None

def integer(value, low=0, high=2**64-1):
    return type(value) is int and low <= value <= high


def number(value): return type(value) in (int,float) and math.isfinite(value)


def strict_json(data):
    def pairs(items):
        out={}
        for key,value in items:
            require(key not in out, 'duplicate JSON key: '+key)
            out[key]=value
        return out
    def constant(value): raise ValueError('nonfinite JSON constant: '+value)
    return json.loads(data, object_pairs_hook=pairs, parse_constant=constant)


class Bound:
    """Consume each immutable byte buffer once, including the bytes hashed."""
    def __init__(self, root, hashes):
        self.root=Path(root).resolve(); self.hashes=hashes; self.cache={}; self.reads={}
        require(type(hashes) is dict, 'hash manifest map required')
        for name,sha in hashes.items():
            self.path(name);require(valid_sha(sha),'invalid artifact digest')
    def path(self,name):
        require(type(name) is str and name and not PurePosixPath(name).is_absolute()
                and all(p not in ('','..','.') for p in name.split('/')) and '\\' not in name,
                'invalid relative artifact path')
        path=self.root/name
        require(path.resolve().is_relative_to(self.root), 'artifact escapes root')
        cursor=self.root
        for part in PurePosixPath(name).parts:
            cursor=cursor/part;require(not cursor.is_symlink(),'symlink artifact path')
        return path
    def read(self,name,limit=4*1024*1024):
        require(name in self.hashes, 'artifact absent from hashes: '+name)
        if name not in self.cache:
            path=self.path(name)
            require(path.is_file() and path.stat().st_size<=limit,'missing/oversized artifact: '+name)
            with path.open('rb') as stream:data=stream.read(limit+1)
            require(len(data)<=limit,'oversized read: '+name)
            require(digest(data)==self.hashes[name],'artifact hash mismatch: '+name)
            self.cache[name]=data;self.reads[name]={'bytes':len(data),'sha256':digest(data)}
        data=self.cache[name];require(len(data)<=limit,'cached artifact exceeds bound')
        return data
    def json(self,name,limit=4*1024*1024): return strict_json(self.read(name,limit))
    def jsonl(self,name,limit=128*1024*1024):
        return [strict_json(line) for line in self.read(name,limit).splitlines() if line.strip()]


def read_pinned(path,expected,limit):
    require(valid_sha(expected),'explicit valid receipt SHA required')
    path=Path(path);require(path.is_file() and path.stat().st_size<=limit,'missing/oversized pinned file')
    with path.open('rb') as stream:data=stream.read(limit+1)
    require(len(data)<=limit and digest(data)==expected,'pinned file hash mismatch')
    return data


def shader_bindings(build,receipt):
    entries=receipt.get('renderer_shaders')
    require(type(entries) is dict and set(entries)=={'reprojection.vert','reprojection.frag'},'both renderer shaders required')
    result={}
    for name,item in entries.items():
        require(type(item) is dict and item.get('literal_equivalent') is True,'shader literal equivalence not asserted')
        for path_key,sha_key in [('path','sha256'),('embedded_path','embedded_sha256')]:
            require(valid_sha(item.get(sha_key)) and build.hashes.get(item.get(path_key))==item[sha_key], 'shader/build digest mismatch')
        raw=build.read(item['path'],4*1024*1024)
        literal=build.read(item['embedded_path'],16*1024*1024)
        require(len(raw)>=20 and len(raw)%4==0 and raw[:4]==struct.pack('<I',0x07230203),'invalid SPIR-V bytes')
        require(re.fullmatch(rb'\s*(?:0x[0-9a-fA-F]{1,8}\s*,\s*)+',literal) is not None,'embedded shader literal grammar')
        words=[int(x,16) for x in re.findall(rb'0x[0-9a-fA-F]{1,8}',literal)]
        require(struct.pack('<'+'I'*len(words),*words)==raw,'embedded/raw renderer shader differs')
        result[name]=dict(item,actual_bytes=len(raw),literal_equivalence_recomputed=True)
    return result


def config_and_build(run,receipt,build):
    provenance=run.json('provenance.json');cleanup=run.json('cleanup.json');summary=run.json('summary.json')
    require(provenance.get('quality_capture') is True,'quality capture was not enabled')
    require(type(provenance.get('headset_serial')) is str and provenance['headset_serial'].strip(),'actual recorded serial missing')
    require(provenance.get('mode') in ('custom-vr444','custom-rrb32'),'unsupported captured decoder route')
    require(cleanup.get('restored') is True and cleanup.get('cleanup_errors')==[]
            and 'run_error' not in cleanup and cleanup.get('app_pid_observed') is True,'run/cleanup did not complete')
    require(summary.get('status')=='pass','native correctness/evidence summary did not pass')
    settings=run.json('client-config-readback.json')
    require(type(settings.get('codec-test-quality-capture.json')) is str,'quality config readback missing')
    config=strict_json(settings['codec-test-quality-capture.json'])
    require(type(config) is dict and set(config)=={'enabled','max_pairs','frame_stride'} and config['enabled'] is True,'quality config fields')
    require(integer(config['max_pairs'],1,8) and integer(config['frame_stride'],1,900),'quality config bounds')
    require(type(provenance.get('quality_max_pairs')) is int and provenance['quality_max_pairs']==config['max_pairs']
            and type(provenance.get('quality_frame_stride')) is int and provenance['quality_frame_stride']==config['frame_stride'],'quality config/provenance mismatch')
    require(type(receipt.get('schema')) is int and receipt['schema']==1,'build receipt schema')
    for key,path in BINARIES.items():
        require(valid_sha(provenance.get(key)) and provenance[key]==build.hashes.get(path),'binary/runtime build binding mismatch: '+key)
    helpers=provenance.get('source_helper_sha256')
    require(type(helpers) is dict and all(name in helpers for name in SOURCES),'missing capture source helpers')
    for name,sha in helpers.items():
        require(valid_sha(sha) and build.hashes.get(name)==sha,'source helper/build binding mismatch: '+name)
        build.read(name,8*1024*1024)
    for key,name in [('client_decoder_source_sha256','client/decoder/test_decoder.cpp'),('application_source_sha256','client/application.cpp')]:
        require(valid_sha(provenance.get(key)) and build.hashes.get(name)==provenance[key],'native source provenance mismatch: '+name)
        build.read(name,8*1024*1024)
    require(valid_sha(provenance.get('harness_sha256')),'missing recorded harness digest')
    return provenance,config,shader_bindings(build,receipt)


def validate_meta(meta,capture_id,eye):
    require(type(meta) is dict and type(meta.get('schema')) is int and meta['schema']==1,'capture metadata schema')
    for key,value in [('capture_id',capture_id),('eye',eye),('array_layer',eye)]:
        require(type(meta.get(key)) is int and meta[key]==value,'capture identity type/value: '+key)
    for key in ['frame','warp','decoder_generation_first_frame','swapchain_image_index']:
        require(integer(meta.get(key)),'capture integer identity: '+key)
    require(meta['frame']>=meta['decoder_generation_first_frame'],'captured frame precedes decoder generation')
    require(meta.get('source_boundary')=='submitted-image','forged/unknown source boundary')
    for key in ['readback_fence_observed','projection_submitted','transfer_src_usage','srgb_encoded_rgb','alpha_unorm']:
        require(meta.get(key) is True,'required capture proof flag: '+key)
    for key in ['quality_pass','latency_eligible','displayed_pixels_verified','independent_reference','runtime_filter_effect_verified']:
        require(meta.get(key) is False,'capture overclaims qualification: '+key)
    for key in ['successful_submit_xr_ns','predicted_display_xr_ns']:
        require(integer(meta.get(key),1,2**63-1),'capture timestamp: '+key)
    w,h=meta.get('width'),meta.get('height');quality.shape(w,h)
    require(type(meta.get('row_bytes')) is int and meta['row_bytes']==w*4 and type(meta.get('bytes')) is int and meta['bytes']==w*h*4,'capture byte geometry')
    require(meta.get('row_order')=='top-to-bottom','unsupported row order')
    expected={43:('RGBA',True),50:('BGRA',False)}
    require(type(meta.get('vk_format')) is int and meta['vk_format'] in expected,'unsupported actual format')
    order,canonical=expected[meta['vk_format']]
    require(meta.get('channel_order')==order and meta.get('canonical_rgba') is canonical,'format/channel contradiction')
    rect=meta.get('subimage_rect')
    require(type(rect) is list and len(rect)==4 and all(type(v) is int for v in rect) and rect==[0,0,w,h],'actual subimage rectangle')
    fov=meta.get('unwarped_fov')
    require(type(fov) is list and type(meta.get('render_fov_left_right_down_up_radians')) is list
            and all(number(v) for v in meta['render_fov_left_right_down_up_radians'])
            and meta['render_fov_left_right_down_up_radians']==fov,'unwarped/render FOV mismatch')
    x,y=quality.perspective_edges(w,h,fov);mask=quality.protected_mask(w,h,x,y)
    input_fov=meta.get('encoded_input_fov_left_right_down_up_radians');quality.perspective_edges(w,h,input_fov)
    for key in ['render_pose','encoded_input_pose']:
        pose=meta.get(key);require(type(pose) is dict and set(pose)=={'position','orientation_xyzw'},'pose fields')
        require(type(pose['position']) is list and len(pose['position'])==3 and all(number(v) for v in pose['position']),'pose position')
        require(type(pose['orientation_xyzw']) is list and len(pose['orientation_xyzw'])==4
                and all(number(v) for v in pose['orientation_xyzw']) and sum(v*v for v in pose['orientation_xyzw'])>0,'pose orientation')
    for key in ['rect_rgb','rect_a']:
        rect=meta.get(key);require(type(rect) is list and len(rect)==4 and all(integer(v,0,4096) for v in rect),'input rectangle')
    iw,ih=meta['rect_rgb'][2:];quality.shape(iw,ih)
    require(meta.get('encoded_input_extent')==[iw,ih] and all(type(v) is int for v in meta['encoded_input_extent']),'encoded input extent')
    for key in ['foveation_x','foveation_y']:
        v=meta.get(key);require(type(v) is list and 1<=len(v)<=4095 and len(v)%2==1 and all(integer(n,0,65535) for n in v),'foveation vector')
    for values,source,target in [(meta['foveation_x'],iw,w),(meta['foveation_y'],ih,h)]:
        require(sum(values)==source and sum(n*(abs((len(values)-1)//2-i)+1) for i,n in enumerate(values))==target,'actual foveation geometry differs')
    for key in ['scale_rgba','bias_rgba']:
        require(type(meta.get(key)) is list and len(meta[key])==4 and all(number(v) for v in meta[key]),'color transform')
    masks=meta.get('cutout_mask');require(type(masks) is list and len(masks)==3
        and all(type(row) is list and len(row)==4 and all(number(v) for v in row) for row in masks),'cutout transform')
    for key in ['output_alpha','sampled_rgb_is_linear','sampler_contract_source_bound','sampler_runtime_queried',
                'alpha_image_bound','alpha_sampler_bound','hmd_needs_srgb_conversion','shader_srgb_decode',
                'input_view_format_source_bound','filter_extension_enabled','shader_bytes_bound_by_collector']:
        require(type(meta.get(key)) is bool,'typed metadata flag: '+key)
    require(meta['sampler_contract_source_bound'] is True and meta['sampler_runtime_queried'] is False
            and meta['input_view_format_source_bound'] is True and type(meta.get('input_view_vk_format')) is int
            and meta['input_view_vk_format']==43,'unknown test decoder image/sampler contract')
    expected_sampler={'origin':'test_decoder::rgb_sampler','min':'LINEAR','mag':'LINEAR','mipmap':'NEAREST',
        'address_uvw':'CLAMP_TO_EDGE','normalized_coordinates':True,'min_lod':0,'max_lod':0,'lod_bias':0,
        'anisotropy':False,'max_anisotropy':0,'compare':False,'compare_op':'NEVER','border_color':'FLOAT_TRANSPARENT_BLACK'}
    sampler=meta.get('sampler_contract')
    require(type(sampler) is dict and set(sampler)==set(expected_sampler)
            and all(type(sampler[k]) is type(v) and sampler[k]==v for k,v in expected_sampler.items()),'test sampler state mismatch')
    require(meta['alpha_image_bound']==meta['alpha_sampler_bound'],'incomplete alpha image/sampler')
    require(type(meta.get('shader_alpha_specialization')) is int and meta['shader_alpha_specialization']==int(meta['alpha_sampler_bound']),'alpha specialization mismatch')
    require(meta['shader_srgb_decode']==(meta['hmd_needs_srgb_conversion'] and not meta['sampled_rgb_is_linear']),'SRGB specialization mismatch')
    for key in ['requested_filter_flags','submitted_filter_flags','input_image_layout']:
        require(integer(meta.get(key)),'integer render setting: '+key)
    require(meta.get('shader_load_names')==['reprojection.vert','reprojection.frag'],'renderer shader names')
    return mask


def audit(run_root,build_receipt,build_receipt_sha256,build_root=None,run_hashes_sha256=None):
    manifest_path=Path(run_root)/'hashes.json'
    if run_hashes_sha256 is None:
        require(manifest_path.stat().st_size<=16*1024*1024,'oversized run hash manifest')
        with manifest_path.open('rb') as f:manifest_data=f.read(16*1024*1024+1)
        require(len(manifest_data)<=16*1024*1024,'oversized run hash manifest read')
    else:manifest_data=read_pinned(manifest_path,run_hashes_sha256,16*1024*1024)
    run=Bound(run_root,strict_json(manifest_data))
    receipt_data=read_pinned(build_receipt,build_receipt_sha256,16*1024*1024);receipt=strict_json(receipt_data)
    require(type(receipt) is dict,'build receipt object')
    require(build_root is not None or (type(receipt.get('source')) is str and receipt['source']),'build root missing')
    build=Bound(build_root or receipt['source'],receipt.get('sha256'))
    provenance,config,shaders=config_and_build(run,receipt,build)
    rows=run.jsonl('client/codec-test-present.jsonl');joins={}
    for row in rows:
        require(type(row) is dict,'presentation row object')
        keys=['left','right','left_warp','right_warp','end_frame_return_xr_ns','submit_xr_ns','predicted_display_xr_ns']
        require(all(integer(row.get(key),1 if key.endswith('_xr_ns') else 0,2**63-1 if key.endswith('_xr_ns') else 2**64-1) for key in keys),'presentation typed identity/time')
        require(row['left']==row['right'] and row['left_warp']==row['right_warp'],'presentation stereo mismatch')
        require(row['submit_xr_ns']>=row['end_frame_return_xr_ns'],'presentation chronology')
        key=(row['left'],row['left_warp'],row['end_frame_return_xr_ns'])
        require(key not in joins,'duplicate exact presentation join');joins[key]=row
    expected_files={PREFIX+f'capture-{cid}-'+suffix for cid in range(1,config['max_pairs']+1)
                    for suffix in ['eye-0.raw','eye-1.raw','eye-0.json','eye-1.json','pair.json']}
    listed={name for name in run.hashes if name.startswith(PREFIX)}
    require(listed==expected_files,'capture inventory/complete-marker population differs')
    folder=run.path(PREFIX[:-1]);actual={str(p.relative_to(run.root)) for p in folder.rglob('*') if p.is_file() or p.is_symlink()}
    require(actual==expected_files,'unbound or missing capture artifact')
    products={};captures=[];last_frame=None
    for cid in range(1,config['max_pairs']+1):
        stem=f'capture-{cid}';marker=run.json(PREFIX+stem+'-pair.json',4096)
        require(type(marker) is dict and type(marker.get('schema')) is int and marker['schema']==1
                and type(marker.get('capture_id')) is int and marker['capture_id']==cid
                and marker.get('complete_eyes')==[0,1] and all(type(x) is int for x in marker['complete_eyes'])
                and marker.get('source_boundary')=='submitted-image' and marker.get('quality_pass') is False
                and marker.get('latency_eligible') is False,'invalid final stereo completion marker')
        metas=[run.json(PREFIX+stem+f'-eye-{eye}.json',1024*1024) for eye in (0,1)]
        masks=[validate_meta(meta,cid,eye) for eye,meta in enumerate(metas)]
        left,right=metas
        for key in ['frame','warp','decoder_generation_first_frame','successful_submit_xr_ns','predicted_display_xr_ns','width','height','vk_format']:
            require(left[key]==right[key],'misjoined capture stereo field: '+key)
        frame=left['frame'];require(frame%config['frame_stride']==0,'capture frame is off absolute source grid')
        require(last_frame is None or frame-last_frame>=config['frame_stride'],'capture ordering/stride');last_frame=frame
        key=(frame,left['warp'],left['successful_submit_xr_ns']);require(key in joins,'no exact successful presentation join')
        require(joins[key]['predicted_display_xr_ns']==left['predicted_display_xr_ns'],'capture target/presentation mismatch')
        for eye,(meta,mask) in enumerate(zip(metas,masks)):
            raw_name=stem+f'-eye-{eye}.raw';require(meta.get('raw_file')==raw_name,'raw filename identity')
            raw=run.read(PREFIX+raw_name,MAX_RAW);require(len(raw)==meta['bytes'],'actual capture byte count')
            if meta['channel_order']=='BGRA':
                rgba=np.frombuffer(raw,dtype=np.uint8).reshape(-1,4)[:,[2,1,0,3]].copy().tobytes();permutation=[2,1,0,3]
            else:rgba=raw;permutation=[0,1,2,3]
            projection={'schema':1,'kind':'unwarped-perspective','frame':frame,'warp':meta['warp'],'eye':eye,
                'geometry':[meta['width'],meta['height']],'fov_radians':meta['unwarped_fov'],
                'source_boundary':'submitted-image','mapping_source':'actual submitted XR projection viewport/FOV',
                'protected_radius_degrees':10.0,'gaze_mode':'fixed-center'}
            base=stem+f'-eye-{eye}';pixel_path=base+'.rgba';mapping_path=base+'-projection.json';mask_path=base+'-central10-mask.u8'
            products[pixel_path]=rgba;products[mapping_path]=(json.dumps(projection,sort_keys=True,indent=2)+'\n').encode();products[mask_path]=mask.astype(np.uint8).tobytes()
            captures.append({'capture_id':cid,'frame':frame,'warp':meta['warp'],'eye':eye,'geometry':projection['geometry'],
                'source_boundary':'submitted-image','channel_format':'RGBA8','candidate':{'path':pixel_path,'sha256':digest(rgba)},
                'projection':{'path':mapping_path,'sha256':digest(products[mapping_path])},
                'protected_mask':{'path':mask_path,'sha256':digest(products[mask_path]),'format':'one u8 per pixel; 0/1; row-major'},
                'protected_pixels':int(mask.sum()),'raw_input':{'path':PREFIX+raw_name,'sha256':digest(raw)},
                'channel_permutation':permutation,'permutation_only':True,'pixel_values_color_converted':False,
                'successful_submit_xr_ns':meta['successful_submit_xr_ns'],'presentation_join':'exact frame/warp/end_frame_return_xr_ns',
                'metadata_input':{'path':PREFIX+base+'.json','sha256':run.hashes[PREFIX+base+'.json']},
                'independent_reference':None,'quality_pass':False,'full_goal_qualified':False})
    report={'schema':1,'status':'pass','scope':'Bound complete native submitted-image acquisition and canonical candidate descriptors only',
            'captured_pairs':config['max_pairs'],'headset_serial_recorded':provenance['headset_serial'],'config':config,'captures':captures,
            'renderer_shaders':shaders,'run_hashes_sha256':digest(manifest_data),'run_hashes_expected_sha_supplied':run_hashes_sha256 is not None,
            'build_receipt_sha256':digest(receipt_data),'build_binding_scope':'Pinned receipt plus actual helper/shader bytes; binary digests match recorded acquisition provenance',
            'run_inputs':run.reads,'build_inputs':build.reads,'recorded_harness_sha256':provenance['harness_sha256'],
            'auditor_sources':{p.name:digest(p.read_bytes()) for p in [Path(__file__),HERE/'research_quality.py']},
            'independent_reference_present':False,'missing_evidence':['independent same-render raw reference','visible headset assessment'],
            'submitted_pixel_equality_verified':False,'displayed_pixels_verified':False,'quality_pass':False,
            'latency_eligible':False,'full_goal_qualified':False,'promotion_eligible':False}
    products['bound-build-receipt.json']=receipt_data
    return report,products


def write_output(output,report,products):
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    for name,data in products.items():
        with (output/name).open('xb') as f:f.write(data)
    with (output/'audit.json').open('x') as f:json.dump(report,f,indent=2);f.write('\n')


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run-root',type=Path,required=True)
    p.add_argument('--build-receipt',type=Path,required=True);p.add_argument('--build-receipt-sha256',required=True)
    p.add_argument('--build-root',type=Path);p.add_argument('--run-hashes-sha256');p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();report,products=audit(a.run_root,a.build_receipt,a.build_receipt_sha256,a.build_root,a.run_hashes_sha256)
    write_output(a.out,report,products);print(json.dumps({'status':'pass','captured_pairs':report['captured_pairs'],'quality_pass':False}))

if __name__=='__main__':main()
