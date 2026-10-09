#!/usr/bin/env python3
"""Build an original-app RLP1 reference after recomputing actual source mappings.

No device or graphics calls. Pixel operations are a checked crop/row permutation
only. The native full-resolution identity raster is a defined reference, not a
claim that the candidate's uncaptured variable-partition VBO was reconstructed.
"""
import argparse
import copy
import json
from pathlib import Path
import struct

import original_source_pixels as pixels
import source_mapping as mapping
import submitted_reference_job as encoded_job

capture = mapping.capture
require = capture.require
BOUNDARY = 'original-application-swapchain-rect-flip-only'


def identity_axis(size):
    require(capture.integer(size, 1, 4096), 'identity axis bound')
    # Match qualified replay.cpp's binary32 statement order. Admission requires
    # exactly the independently specified full-screen endpoints, not epsilon.
    def single(v): return struct.unpack('<f', struct.pack('<f', v))[0]
    origin = single(-0.5*size)
    step = single(2.0/size)
    require(single(origin*step) == -1.0
            and single(single(origin+size)*step) == 1.0,
            'identity geometry does not give exact full-screen endpoints')
    return [size]


def original_eye(meta, record, eye, source, app_view, encoded):
    prepared=pixels.prepare(record,eye,source,meta,app_view,encoded_rgba=encoded)
    e=record['eyes'][eye];w,h=meta['width'],meta['height']
    rgba=prepared['expected_rgba']
    private=copy.deepcopy(meta)
    private.update(encoded_input_extent=[w,h],rect_rgb=[0,0,w,h],
                   foveation_x=identity_axis(w),foveation_y=identity_axis(h))
    payload=encoded_job.replay_eye(private,rgba)
    desc=dict(eye=eye,frame=record['frame'],warp=record['warp'],geometry=[w,h],
        original_geometry=[e['source_width'],e['source_height']],source_rectangle=e['src_rect'],
        row_flip=record['flip_y'],source_boundary=BOUNDARY,
        original_source_sha256=capture.digest(source),reference_input_sha256=capture.digest(rgba),
        rgba_bytes=len(rgba),channel_permutation=[0,1,2,3],resizing=False,color_conversion=False,
        identity_foveation_x=[w],identity_foveation_y=[h],exact_ndc_endpoints=[-1,1],
        push_constant_sha256=capture.digest(payload[24:24+112]),
        submitted_pose=meta['render_pose'],image_index=e['image_index'],
        swapchain_id=e['swapchain_id'],image_id=e['image_id'],image_view_id=e['image_view_id'],
        source_vk_format=e['source_vk_format'],view_vk_format=e['view_vk_format'],
        encoded_payload_pixels_used_as_reference=False,decoder_output_used_as_reference=False,
        post_foveation_pixels_used_as_reference=False,original_pixel_proof=prepared['proof'])
    return payload,desc,prepared


def make_job(run_root, run_sha, build_receipt, build_sha, application_build_receipt,
             application_build_sha, capture_id, build_root=None, *, _context=None):
    require(_context is None or type(_context) is dict and not _context,'fresh internal pixel context')
    require(capture.integer(capture_id,1,8), 'typed bounded capture ID')
    own_sources={str(p):capture.digest(p.read_bytes()) for p in
                 (Path(__file__),Path(mapping.__file__),Path(encoded_job.__file__),Path(capture.__file__),Path(pixels.__file__))}
    proof = mapping.audit(run_root, run_sha, build_receipt, build_sha,
                          application_build_receipt, application_build_sha, build_root)
    manifest = capture.read_pinned(Path(run_root)/'hashes.json',run_sha,16*1024*1024)
    run = capture.Bound(run_root,capture.strict_json(manifest))
    # Reconsume the same immutable bytes audited above. Concurrent changes are
    # rejected; a claimed PASS report is never a substitute for recomputation.
    for name,desc in proof['run_inputs'].items():
        require(capture.digest(run.read(name,max(desc['bytes'],1))) == desc['sha256'], 'source audit bytes changed')
    records = mapping.records(run.jsonl(mapping.TRACE,32*1024*1024))
    joined,_ = mapping.join(records,run.jsonl('app-source/app.jsonl',32*1024*1024),run.jsonl('host/source-trace.jsonl'))
    native = proof['native_capture_audit']
    require(capture_id <= native['captured_pairs'], 'capture outside actual population')
    metas = [run.json(capture.PREFIX+f'capture-{capture_id}-eye-{e}.json',1024*1024) for e in (0,1)]
    frame,warp = metas[0]['frame'],metas[0]['warp']
    matches = [r for r in records if (r['frame'],r['warp']) == (frame,warp)]
    require(len(matches) == 1, 'unique exact original source mapping required')
    record=matches[0];j=joined[frame]
    require(any((p['frame'],p['warp']) == (frame,warp) for p in proof['pairs']), 'mapping not joined to saved encoded pair')
    output={k:metas[0][k] for k in ('width','height','vk_format')}
    job=bytearray(struct.pack('<6I',0x31504c52,1,output['width'],output['height'],output['vk_format'],2))
    sources=[];prepared_eyes=[];native_raw=[]
    for eye,meta in enumerate(metas):
        name=f'app-source/eye{eye}.source.rgba'
        raw=run.read(name,16*1024*1024)
        require(capture.digest(raw) == proof['application_audit']['source_sha256'][eye]
                == j['application']['views'][eye]['source_sha256'], 'literal original application byte identity')
        encoded=run.read(f'host/{frame}-{eye}.rgba',16*1024*1024)
        payload,desc,prepared=original_eye(meta,record,eye,raw,j['application']['views'][eye],encoded)
        job.extend(payload);prepared_eyes.append(prepared)
        native_raw.append(run.read(capture.PREFIX+f'capture-{capture_id}-eye-{eye}.raw',16*1024*1024))
        desc.update(source=dict(path=name,sha256=capture.digest(raw),bytes=len(raw)),
                    application_sequence=j['application']['sequence'],pacer_id=j['pacer_id'],
                    app_frame_id=j['app_frame_id'],layer_target_host_ns=record['layer_target_host_ns'])
        sources.append(desc)
    require(len(job)<=40*1024*1024,'bounded complete stereo RLP1 job')
    job=bytes(job);job_name=f'capture-{capture_id}-original-application-input.rlp'
    # End checks inspect every audited input, not merely each transformed image.
    for key,desc in proof['run_inputs'].items():
        capture.read_pinned(run.path(key),desc['sha256'],max(desc['bytes'],1))
    build_directory=Path(build_root or capture.strict_json(capture.read_pinned(build_receipt,build_sha,16*1024*1024))['source'])
    for key,desc in proof['build_inputs'].items():
        capture.read_pinned(build_directory/key,desc['sha256'],max(desc['bytes'],1))
    capture.read_pinned(Path(run_root)/'hashes.json',run_sha,16*1024*1024)
    capture.read_pinned(application_build_receipt,application_build_sha,4*1024*1024)
    capture.read_pinned(build_receipt,build_sha,16*1024*1024)
    for path,sha in own_sources.items():capture.read_pinned(path,sha,1024*1024)
    for auditor_name,sha in proof.get('auditor_sha256',{}).items():
        capture.read_pinned(mapping.ROOT/auditor_name,sha,1024*1024)
    report=dict(schema=1,status='pass',capture_id=capture_id,frame=frame,warp=warp,
        job=dict(path=job_name,sha256=capture.digest(job),bytes=len(job),format='RLP1',version=1),output=output,
        source_boundary=BOUNDARY,independent_inputs=sources,candidate_capture_audit=native,
        renderer_shaders=native['renderer_shaders'],source_mapping_audit=proof,
        run_hashes_sha256=run_sha,build_receipt_sha256=build_sha,
        application_build_receipt_sha256=application_build_sha,run_inputs=run.reads,build_inputs=proof['build_inputs'],
        adapter_sources={p.name:capture.digest(p.read_bytes()) for p in
                         (Path(__file__),Path(mapping.__file__),Path(encoded_job.__file__),Path(capture.__file__),Path(pixels.__file__))},
        reference_geometry='independently defined full-resolution identity quad with exact NDC endpoints',
        variable_partition_geometry_reconstructed=False,actual_native_vertex_bytes_captured=False,
        actual_native_push_constant_bytes_captured=False,source_mapping_recomputed=True,
        original_application_bytes_used=True,post_foveation_pixels_used_as_reference=False,
        physical_pose_scope='Same recorded XR submitted pose/FOV; private shader has no pose transform. No runtime timewarp or optical claim.',
        full_goal_qualified=False,quality_pass=False,displayed_pixels_verified=False,latency_eligible=False,
        scope='Prepared independent literal original-app reference; actual same-device rendering and candidate comparison are still required')
    if _context is not None:_context.update(prepared=prepared_eyes,native_raw=native_raw)
    return report,{job_name:job}


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run-root','build-receipt','application-build-receipt','out'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('run-sha','build-sha','application-build-sha'):p.add_argument('--'+name,required=True)
    p.add_argument('--capture-id',type=int,required=True);p.add_argument('--build-root',type=Path)
    a=p.parse_args();report,products=make_job(a.run_root,a.run_sha,a.build_receipt,a.build_sha,
        a.application_build_receipt,a.application_build_sha,a.capture_id,a.build_root)
    capture.write_output(a.out,report,products)
    print(json.dumps(dict(status=report['status'],job=report['job'],quality_pass=False)))

if __name__=='__main__':main()
