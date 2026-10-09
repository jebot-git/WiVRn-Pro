#!/usr/bin/env python3
"""Bound actual host UBOs to original application texels; never a full Q pass."""
import argparse
import importlib.util
import json
import math
from pathlib import Path
import struct
import tempfile

import numpy as np

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
capture=module('source_mapping_capture',HERE/'submitted_capture.py')
job=module('source_mapping_job',HERE/'submitted_reference_job.py')
application_control=module('source_mapping_application_control',HERE/'quality_application.py')
app=module('source_mapping_app',ROOT/'prototypes/vr444/experiments/quality_stimuli/audit.py')
fresh=module('source_mapping_fresh',ROOT/'prototypes/vr444/experiments/compositor_freshness/analyze.py')
require=capture.require
TRACE='host/source-mapping.jsonl'
SOURCES=('common/codec_test/source_mapping.h','server/compositor/foveation.h','server/compositor/foveation.cpp',
         'server/compositor/compositor.cpp','server/compositor/shaders/foveation_rgb.comp')
HEADER=dict(type='header',schema=1,capacity=128,sample_stride=90,max_axis=4096,max_params=129,
    shader='foveation_rgb.comp',sampling='integer_texelFetch_max4x4_average_linear_then_srgb',
    opaque_rgba_srgb_only=True,quality_only=True,latency_eligible=False,native_handle_scope='process_local_only')

def integer(v,lo=0,hi=2**64-1):
    require(capture.integer(v,lo,hi),'source mapping integer bound');return v

def integers(v,lo,hi,count=None):
    require(type(v) is list and (count is None or len(v)==count),'source mapping vector length')
    for x in v:integer(x,lo,hi)
    return v

def axis(params,edges,count,offset,extent,flip):
    integers(params,0,4096);require(0<len(params)<=129 and len(params)%2==1,'odd bounded ratio population')
    integers(edges,0,4096,count+1)
    require(sum(params)==count,'ratio count/output dimension mismatch')
    mid=len(params)//2;weights=[abs(i-mid)+1 for i,n in enumerate(params) for _ in range(n)]
    require(sum(weights)==extent,'ratio/source extent mismatch')
    expected=[offset+extent if flip else offset]
    for weight in weights:expected.append(expected[-1]+(-weight if flip else weight))
    require(edges==expected,'actual UBO edges disagree with declared parameters/rectangle/flip')
    return np.asarray(edges,dtype=np.int64)

def records(rows):
    require(type(rows) is list and 3<=len(rows)<=130 and all(type(r) is dict for r in rows),'bounded source mapping population')
    require(set(rows[0])==set(HEADER) and all(type(rows[0][k]) is type(v) and rows[0][k]==v for k,v in HEADER.items()),'source mapping header contract')
    footer=rows[-1]
    require(set(footer)=={'type','records','dropped','failed','complete'} and footer.get('type')=='footer' and footer.get('complete') is True,'complete source mapping footer')
    require(integer(footer.get('records'),1,128)==len(rows)-2 and integer(footer.get('dropped'))==integer(footer.get('failed'))==0,'missing/failed source mapping records')
    result=[];last_frame=-1;last_sem=0;last_complete=0;owner=None;objects={};chains={}
    for sequence,r in enumerate(rows[1:-1],1):
        require(r.get('type')=='mapping' and integer(r.get('sequence'),1,128)==sequence,'mapping sequence')
        frame=integer(r.get('frame'),0,2**63-1);integer(r.get('warp'));sem=integer(r.get('semaphore_value'),1)
        require(frame%90==0 and frame>last_frame and sem>last_sem,'mapping frame/producer order')
        compositor=integer(r.get('compositor_id'),1);require(owner in (None,compositor),'multiple compositor instances')
        target=integer(r.get('layer_target_host_ns'),1,2**63-1)
        submit=integer(r.get('submit_return_host_ns'),1,2**63-1);complete=integer(r.get('complete_host_ns'),submit,2**63-1)
        require(submit>=last_complete,'overlapping mapped UBO producer lifetimes')
        require(r.get('producer_complete') is True and r.get('direct_projection') is True and r.get('view_space') is False,'unsupported direct/local producer')
        flags=integer(r.get('layer_flags'));require(not flags&8,'VIEW-space layer cannot qualify original source map')
        require(integer(r.get('layer_count'),1,1)==1 and integer(r.get('layer_index'),0,0)==0 and integer(r.get('layer_type'),0,1) in (0,1),'projection layer shape')
        require(type(r.get('flip_y')) is bool,'typed flip')
        w,h=integer(r.get('width'),1,4096),integer(r.get('height'),1,4096);require(w*h<=4194304,'encoded pixel cap')
        require(type(r.get('eyes')) is list and len(r['eyes'])==2,'complete stereo mapping')
        for eye,e in enumerate(r['eyes']):
            require(type(e) is dict and integer(e.get('eye'),0,1)==eye,'mapping eye identity')
            sw,sh=integer(e.get('source_width'),1,4096),integer(e.get('source_height'),1,4096);require(sw*sh<=4194304,'source pixel cap')
            for key in ('source_array_size','source_face_count','source_sample_count','source_mip_count'):integer(e.get(key),1,1)
            count=integer(e.get('source_image_count'),1,16);integer(e.get('image_index'),0,count-1);integer(e.get('array_index'),0,0)
            for key in ('source_vk_format','view_vk_format'):integer(e.get(key),43,43)
            integer(e.get('source_create_flags'))
            require(e.get('view_format_provenance')=='pinned_comp_swapchain_view_creation','unknown view-format source')
            chain=integer(e.get('swapchain_id'),1);image=integer(e.get('image_id'),1);view=integer(e.get('image_view_id'),1)
            require(eye not in chains or chains[eye]==chain,'eye swapchain changed within fixed application')
            chains[eye]=chain;key=(eye,e['image_index']);obj=(chain,image,view)
            require(key not in objects or objects[key]==obj,'same released image index changed native object')
            objects[key]=obj
            rect=integers(e.get('src_rect'),0,4096,4)
            require(rect[2]>0 and rect[3]>0 and rect[0]+rect[2]<=sw and rect[1]+rect[3]<=sh,'positive bounded source rectangle')
            bits=integers(e.get('source_fov_bits_lrud'),0,2**32-1,4)
            angles=struct.unpack('<4f',struct.pack('<4I',*bits))
            require(all(math.isfinite(a) for a in angles) and -math.pi/2<angles[0]<angles[1]<math.pi/2 and -math.pi/2<angles[3]<angles[2]<math.pi/2,'source FOV bits')
            axis(e.get('x_params'),e.get('x_edges'),w,rect[0],rect[2],False)
            axis(e.get('y_params'),e.get('y_edges'),h,rect[1],rect[3],r['flip_y'])
        require(chains[0]!=chains[1] and r['eyes'][0]['image_id']!=r['eyes'][1]['image_id'] and r['eyes'][0]['image_view_id']!=r['eyes'][1]['image_view_id'],'stereo native image alias')
        result.append(r);owner=compositor;last_frame=frame;last_sem=sem;last_complete=complete
    return result

def compare_eye(record,eye,source,encoded,app_view):
    """Exact byte comparison only where consumed footprint is exactly one texel.

    Source mask is original application rays. A nonunit footprint never qualifies
    original pixel preservation, even if a repeated/constant pattern hides it.
    """
    e=record['eyes'][eye];sw,sh=e['source_width'],e['source_height'];w,h=record['width'],record['height']
    require(len(source)==sw*sh*4 and len(encoded)==w*h*4,'source/output byte geometry')
    require(e['src_rect']==[0,0,sw,sh],'initial fixture route requires full positive source rectangle')
    require(app_view['width']==sw and app_view['height']==sh and app_view['image_index']==e['image_index'],'actual released image/shape join')
    require(app_view['fov']['bits']==e['source_fov_bits_lrud'],'actual application/source FOV bit join')
    fov=app_view['fov']['radians'];px,py=capture.quality.perspective_edges(sw,sh,[fov[0],fov[1],fov[3],fov[2]])
    mask=capture.quality.protected_mask(sw,sh,px,py)
    xe=np.asarray(e['x_edges'],dtype=np.int64);ye=np.asarray(e['y_edges'],dtype=np.int64)
    dx=np.diff(xe);dy=np.abs(np.diff(ye));xlo=xe[:-1];ylo=np.minimum(ye[:-1],ye[1:])
    # Every original texel has exactly one consumed cell because the complete
    # UBO axis was checked as a strict tiling of the full source rectangle.
    xi=np.repeat(np.arange(w),dx)
    yi=np.repeat(np.arange(h),dy)
    if record['flip_y']:yi=yi[::-1]
    sy,sx=np.nonzero(mask);cx,cy=xi[sx],yi[sy]
    unit=(dx[cx]==1)&(dy[cy]==1)
    src=np.frombuffer(source,dtype=np.uint8).reshape(sh,sw,4)
    dst=np.frombuffer(encoded,dtype=np.uint8).reshape(h,w,4)
    expected=src[sy[unit],sx[unit]];actual=dst[cy[unit],cx[unit]]
    diff=np.abs(actual[:,:3].astype(np.int16)-expected[:,:3].astype(np.int16))
    mismatches=int(np.any(diff!=0,axis=1).sum());alpha=int((actual[:,3]!=expected[:,3]).sum())
    protected=int(mask.sum());covered=int(unit.sum())
    require(protected>0,'empty original-source protected cone')
    # Conservative one encoded-cell halo, reported separately. This does not
    # reconstruct native raster interpolation or prove final display samples.
    cells=np.zeros((h,w),bool);cells[cy,cx]=True;halo=cells.copy()
    for oy in (-1,0,1):
        for ox in (-1,0,1):
            ya,yb=max(0,oy),min(h,h+oy);xa,xb=max(0,ox),min(w,w+ox)
            halo[ya:yb,xa:xb]|=cells[ya-oy:yb-oy,xa-ox:xb-ox]
    nonunit=(dy[:,None]!=1)|(dx[None,:]!=1)
    opaque=bool(np.all(src[:,:,3]==255) and np.all(dst[:,:,3]==255))
    return dict(eye=eye,original_source_geometry=[sw,sh],encoded_geometry=[w,h],protected_original_texels=protected,
        protected_texels_with_single_texel_footprint=covered,protected_texels_with_averaged_footprint=protected-covered,
        protected_one_to_one_rgb_mismatched_pixels=mismatches,protected_one_to_one_rgb_max_error=int(diff.max()) if diff.size else None,
        protected_one_to_one_alpha_mismatched_pixels=alpha,opaque_alpha_both=opaque,
        protected_source_stage_exact=covered==protected and mismatches==alpha==0 and opaque,
        selected_encoded_cells=int(cells.sum()),one_cell_halo_encoded_cells=int(halo.sum()),
        one_cell_halo_nonunit_footprints=int((halo&nonunit).sum()),
        shader_source_sampling='integer texelFetch; no sampler interpolation; up to4x4 linear samples averaged then SRGB UNORM',
        source_srgb_roundtrip_assumed_exact=False,actual_source_codes_compared=True,
        downstream_filter_or_raster_equivalence_claim=False)

def join(rows,app_rows,source_rows):
    evaluated=fresh.evaluate(source_rows,[r['frame'] for r in rows])
    require(evaluated['status']=='pass' and evaluated['ambiguous_projection_matches']==0,'complete unambiguous source/latch trace')
    require(app_rows[0].get('schema')==3 and app_rows[0].get('source_reference_space')=='LOCAL','schema3 converted LOCAL application required')
    apps={r['predicted_display_time_monotonic_ns']:r for r in app_rows if r['type']=='frame' and r['projection_submitted']}
    require(len(apps)==sum(r['type']=='frame' and r['projection_submitted'] for r in app_rows),'duplicate converted application target')
    systems={r['system_frame_id']:r for r in source_rows if r.get('event')=='compositor_frame'}
    delivered={(r['pacer_id'],r['app_frame_id']):r for r in source_rows if r.get('event')=='app_delivered'}
    predicts={(r['pacer_id'],r['app_frame_id']):r for r in source_rows if r.get('event')=='app_predict'}
    ids={r['system_frame_id']:r['app_frames'] for r in evaluated['system_to_app_frames']};result={}
    for r in rows:
        frame=r['frame'];target=r['layer_target_host_ns'];system=systems[frame]
        require(r['layer_type']==0 and r['layer_flags']==0,'fixture submits opaque projection with no layer flags')
        require(system['observed_ns']<=r['submit_return_host_ns'],'source callback follows recorded GPU submit return')
        require(system['direct_projection'] and system['layer_count']==system['projection_count']==1 and system['layer_target_ns']==target,'mapping/source-trace direct layer target')
        require(len(ids[frame])==1,'one source application owner required')
        identity=ids[frame][0];key=identity['pacer_id'],identity['app_frame_id']
        require(delivered[key]['display_time_ns']==predicts[key]['display_time_ns']==target,'delivered/predicted/source target disagreement')
        require(target in apps,'no exact runtime-converted application target')
        selected=apps[target];require(selected['projection_submitted'] is True and len(selected['views'])==2,'actual complete app layer required')
        result[frame]=dict(application=selected,pacer_id=key[0],app_frame_id=key[1])
    return result,evaluated

def audit(run_root,run_sha,build_receipt,build_sha,application_build_receipt,application_build_sha,build_root=None):
    context={};native,_=capture.audit(run_root,build_receipt,build_sha,build_root,run_sha,_context=context)
    run,build,receipt,prov=(context[k] for k in ('run','build','receipt','provenance'))
    require(prov.get('mode')=='custom-rrb32' and prov.get('rrb_source')=='q1' and type(prov.get('custom_q')) is int and prov.get('custom_q')==1 and prov.get('custom_compression')=='none','Q1 RRB no outer compression route')
    require(prov.get('source_mapping') is True and prov.get('source_trace') is True and prov.get('quality_application') is True,'explicit source-map/app/source profile')
    require(prov.get('source_mapping_build_receipt_sha256')==build_sha and prov.get('source_mapping_app_build_receipt_sha256')==application_build_sha,'source mapping recorded/explicit receipt pins differ')
    for name in SOURCES:
        actual=build.read(name,8*1024*1024)
        require(capture.digest(actual)==capture.digest((HERE/'source-overlay'/name).read_bytes()),'audited source-map implementation differs: '+name)
        require(prov['source_helper_sha256'].get(name)==build.hashes[name],'mapping helper absent from native provenance')
    # Same binary/literal check as renderer, retaining the one distinct shader key.
    shader=receipt.get('foveation_shaders');require(type(shader) is dict and set(shader)=={'foveation_rgb.comp'},'actual foveation shader binding')
    item=shader['foveation_rgb.comp'];require(item.get('literal_equivalent') is True,'foveation literal binding missing')
    for k,hk in [('path','sha256'),('embedded_path','embedded_sha256')]:require(build.hashes.get(item.get(k))==item.get(hk) and capture.valid_sha(item.get(hk)),'foveation artifact digest binding')
    raw=build.read(item['path']);literal=build.read(item['embedded_path'],16*1024*1024)
    import re
    require(len(raw)>=20 and len(raw)%4==0 and raw[:4]==struct.pack('<I',0x07230203),'foveation SPIR-V')
    require(re.fullmatch(rb'\s*(?:0x[0-9a-fA-F]{1,8}\s*,\s*)+',literal) is not None,'foveation embedded literal grammar')
    words=[int(x,16) for x in re.findall(rb'0x[0-9a-fA-F]{1,8}',literal)];require(struct.pack('<'+'I'*len(words),*words)==raw,'foveation embedded byte equivalence')
    contract=receipt.get('monado_source_view_contract');require(type(contract) is dict,'external view-format contract required')
    name='src/xrt/compositor/util/comp_swapchain.c';require(type(contract.get('source')) is str and Path(contract['source']).is_absolute() and type(contract.get('files')) is dict and set(contract['files'])=={name},'external contract source')
    path=Path(contract['source'])/name;digest=contract['files'][name]
    external=capture.read_pinned(path,digest,4*1024*1024)
    require(receipt.get('external_artifacts_sha256',{}).get(str(path))==digest,'external view implementation absent from build receipt')
    require(b'vk_format_convert_unorm_to_srgb' in external and b'XRT_SWAPCHAIN_CREATE_SAMPLE_AS_SRGB' in external,'view creation semantic source missing')
    app_receipt=capture.strict_json(capture.read_pinned(application_build_receipt,application_build_sha,4*1024*1024))
    for name in ('app.cpp','pattern.hpp'):
        require(app_receipt.get('source_sha256',{}).get(name)==capture.digest((ROOT/'prototypes/vr444/experiments/quality_stimuli'/name).read_bytes()),'application source implementation differs')
    plan_raw=run.read('quality-application-plan.json',65536);plan=capture.strict_json(plan_raw);execution=run.json('quality-application-execution.json')
    require(capture.digest(plan_raw)==prov.get('quality_application_plan_sha256')==execution.get('original_plan_sha256'),'application original plan identity')
    application_control.contract(plan)
    binary=app_receipt.get('binary_sha256',{}).get('quality-stimuli')
    require(capture.valid_sha(binary) and binary==plan.get('executable_sha256')==execution.get('executable_sha256'),'application executed binary/build identity')
    require(execution.get('snapshot_unchanged') is True and execution.get('snapshot_sha256_after')==binary==prov.get('host_application_sha256'),'actual immutable application snapshot')
    require(run.json('server-config.json').get('application')==execution.get('actual_application')==execution.get('application')==application_control.argv(plan,execution.get('snapshot_path')),'actual canonical application command')
    require(execution.get('source_application_space')=='LOCAL' and execution.get('latency_eligible') is False,'application space/profile')
    # Audit immutable copies, including the complete bound app-source inventory;
    # independently generate expected original pixels before any comparison.
    names={n for n in run.hashes if n.startswith('app-source/')};require(names,'missing application artifacts')
    with tempfile.TemporaryDirectory(prefix='sub15-map-app-') as directory:
        for name in sorted(names):
            dest=Path(directory)/name.removeprefix('app-source/');require(dest.parent==Path(directory),'unexpected nested application file')
            dest.write_bytes(run.read(name,32*1024*1024))
        application=app.application(directory)
    require(all(plan[k]==execution[k] for k in ('pattern','width','height')),'application plan/execution geometry')
    require(application['pattern']==plan['pattern'],'actual application pattern/plan')
    require(application['time_conversion']=='XR_KHR_convert_timespec_time/CLOCK_MONOTONIC','application time bridge absent')
    app_rows=run.jsonl('app-source/app.jsonl',32*1024*1024)
    for field,key in (('width_requested','width'),('height_requested','height'),('frame_limit','frame_limit'),('seconds_limit','seconds_limit')):
        require(app_rows[0].get(field)==plan[key],'actual app header/request mismatch')
    mapped=records(run.jsonl(TRACE,32*1024*1024));source=run.jsonl('host/source-trace.jsonl')
    joined,freshness=join(mapped,app_rows,source)
    host={};expected=set()
    for eye in (0,1):
        for r in run.jsonl(f'host/encode-{eye}.jsonl'):
            require(type(r) is dict,'encode row')
            if r.get('type')=='rrb_pair_rejected':continue
            key=(integer(r.get('frame'),0,2**63-1),integer(r.get('eye'),eye,eye));require(key not in host,'duplicate encode identity');host[key]=r
            if key[0]%90==0:expected.add(key)
    mapping_frames={r['frame'] for r in mapped}
    require(expected and {f for f,e in expected}<=mapping_frames and expected=={(f,eye) for f,_ in expected for eye in (0,1)},'missing sampled encoded stereo/mapping coverage')
    rgba_inventory={f'host/{f}-{e}.rgba' for f,e in expected}
    actual={n for n in run.hashes if re.fullmatch(r'host/[0-9]+-[01]\.rgba',n)}
    require(actual==rgba_inventory,'sampled source capture population differs from encoded events')
    eyes_setup={r['eye']:r for r in app_rows if r['type']=='eye'};pairs=[]
    for r in mapped:
        f=r['frame']
        if (f,0) not in expected:continue
        j=joined[f];views=j['application']['views'];results=[];pair_bytes=0
        for eye in (0,1):
            e=r['eyes'][eye];require(e['source_image_count']==eyes_setup[eye]['swapchain_images'],'application/native image-count join')
            require(views[eye]['source_sha256']==application['source_sha256'][eye],'original source hash join')
            encoded=host[f,eye];wire=run.read(f'host/{f}-{eye}.envelope',262144);header=job.envelope(wire);pair_bytes+=len(wire)
            expected_header=dict(frame=f,warp=r['warp'],eye=eye,width=r['width'],height=r['height'],kind=4,flags=0)
            require(all(header[k]==v for k,v in expected_header.items()),'source map/envelope identity')
            require(encoded.get('warp')==r['warp'] and encoded.get('payload_bytes')==len(wire) and encoded.get('effective_codec')=='rrb32' and type(encoded.get('quantizer')) is int and encoded.get('quantizer')==1 and encoded.get('source_contract')=='post_foveation_rgb_exact','Q1 encode/source event')
            original=run.read(f'app-source/eye{eye}.source.rgba',16*1024*1024);after=run.read(f'host/{f}-{eye}.rgba',16*1024*1024)
            results.append(compare_eye(r,eye,original,after,views[eye]))
        require(pair_bytes<=262144,'whole stereo envelope cap')
        pairs.append(dict(frame=f,warp=r['warp'],app_sequence=j['application']['sequence'],pacer_id=j['pacer_id'],app_frame_id=j['app_frame_id'],layer_target_host_ns=r['layer_target_host_ns'],pair_bytes=pair_bytes,eyes=results))
    return dict(schema=1,status='pass',scope='Actual consumed UBO -> independent original application source-stage comparison',
        mapped_frames=len(mapped),sampled_encoded_pairs=len(pairs),pairs=pairs,
        protected_source_stage_exact=all(e['protected_source_stage_exact'] for p in pairs for e in p['eyes']),
        run_hashes_sha256=run_sha,build_receipt_sha256=build_sha,application_build_receipt_sha256=application_build_sha,
        native_capture_audit=native,application_audit=application,source_trace_audit=freshness,
        foveation_shader=item,monado_source_view_contract=contract,
        run_inputs=run.reads,build_inputs=build.reads,
        auditor_sha256={str(p.relative_to(ROOT)):capture.digest(p.read_bytes()) for p in (Path(__file__),HERE/'quality_application.py',HERE/'submitted_capture.py',HERE/'submitted_reference_job.py',HERE/'research_quality.py',ROOT/'prototypes/vr444/experiments/quality_stimuli/audit.py',ROOT/'prototypes/vr444/experiments/compositor_freshness/analyze.py')},
        original_source_clock_join='checked runtime XR_KHR_convert_timespec_time CLOCK_MONOTONIC target equality; no inferred offset',
        identity_scope='exact converted target + unique delivered/predicted app identity + latch + eye/released image index; process-local handles are not compared across IPC',
        source_srgb_roundtrip_assumed_exact=False,codec_decode_rechecked_here=False,
        downstream_filter_or_raster_equivalence_claim=False,displayed_pixels_verified=False,
        latency_eligible=False,quality_pass=False,full_goal_qualified=False,
        limitations=['Finite captured source-stage texels only; existing codec and submitted-image audits remain separate.',
                    'One-cell halo is conservative mapping evidence, not a reconstruction of native raster/filter support.',
                    'Pinned acquisition/build receipts are trusted provenance; this is not hostile-host authentication.'])

def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run-root','build-receipt','application-build-receipt','out'):p.add_argument('--'+name,type=Path,required=True)
    for name in ('run-sha','build-sha','application-build-sha'):p.add_argument('--'+name,required=True)
    p.add_argument('--build-root',type=Path);a=p.parse_args()
    result=audit(a.run_root,a.run_sha,a.build_receipt,a.build_sha,a.application_build_receipt,a.application_build_sha,a.build_root)
    with a.out.open('x') as out:json.dump(result,out,indent=2);out.write('\n')
if __name__=='__main__':main()
