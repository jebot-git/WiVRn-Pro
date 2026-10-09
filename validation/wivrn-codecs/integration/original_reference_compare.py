#!/usr/bin/env python3
"""Recompute original-app reference and actual-device acquisition, then compare.

No GPU/device calls. A pinned existing replay acquisition is consumed offline.
Both reference-vs-literal-original and actual-submitted-vs-reference code equality
are required. No floating-point/sRGB/filtering identity is presumed.
"""
import argparse
import importlib.util
import json
from pathlib import Path
import struct

import numpy as np
import original_reference_job as original
import submitted_capture as native
import submitted_comparison as common

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]
SPEC=importlib.util.spec_from_file_location('original_replay_capture',ROOT/'prototypes/vr444/experiments/submitted_image_replay/capture.py')
replay=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(replay)
need=common.need


def same_json(a,b,message):
    # Python dict equality alone accepts bool/int aliases.
    need(json.dumps(a,sort_keys=True,separators=(',',':'),allow_nan=False)
         == json.dumps(b,sort_keys=True,separators=(',',':'),allow_nan=False),message)


def literal_inputs(wire,output):
    need(struct.unpack('<6I',wire[:24])==(0x31504c52,1,output['width'],output['height'],output['vk_format'],2),'original job header')
    pos=24;eyes=[]
    for _ in (0,1):
        w,h,nx,ny,a,s=struct.unpack_from('<6I',wire,pos)
        need((w,h,nx,ny,a,s)==(output['width'],output['height'],1,1,0,0),'identity source grammar')
        pos+=24+112
        need(struct.unpack_from('<2I',wire,pos)==(w,h),'identity foveation grammar');pos+=8
        count=w*h*4;eyes.append(wire[pos:pos+count]);need(len(eyes[-1])==count,'original pixel payload');pos+=count
    need(pos==len(wire),'trailing original job bytes')
    return eyes



def audit(run_root,run_sha,build_receipt,build_sha,application_build_receipt,application_build_sha,
          capture_id,job_receipt,job_sha,replay_directory,replay_plan,replay_plan_sha,runner,out,build_root=None):
    out=Path(out);need(out.is_absolute() and not out.exists(),'fresh absolute audit output directory')
    inputs=common.Inputs()
    own_sources={str(p):native.digest(p.read_bytes()) for p in
                 (Path(__file__),Path(original.__file__),Path(common.__file__),Path(native.__file__),Path(replay.__file__),Path(original.pixels.__file__))}
    archived=inputs.json(job_receipt,job_sha)
    context={}
    expected,products=original.make_job(run_root,run_sha,build_receipt,build_sha,
                                        application_build_receipt,application_build_sha,capture_id,build_root,_context=context)
    same_json(archived,expected,'original adapter differs from fresh source-mapping computation')
    wire=inputs.child(Path(job_receipt).parent,archived['job'],40*1024*1024)
    need(wire==products[expected['job']['path']],'original job differs from fresh literal input bytes')
    plan=inputs.json(replay_plan,replay_plan_sha)
    rp=plan['replay']
    need(rp['source_adapter_sha256']==job_sha and rp['job_sha256']==native.digest(wire)
         and rp['native_build_receipt_sha256']==build_sha,'actual replay/job/build join')
    same_json(rp['output'],expected['output'],'reference output geometry')
    out.mkdir(parents=True)
    # This invokes only the read-only collector audit; its runner is hashed,
    # never executed. Native device execution must have occurred beforehand.
    observed=replay.audit(replay_directory,replay_plan,runner,out/'reference')
    need(observed['actual_headset_used'] is True and observed['omission_control_verified'] is True,
         'actual hardware/omission controls required')
    native_report,native_products=native.audit(run_root,build_receipt,build_sha,build_root,run_sha)
    for key in ('run_hashes_sha256','build_receipt_sha256','renderer_shaders','captures','headset_serial_recorded'):
        same_json(native_report[key],expected['candidate_capture_audit'][key],'fresh actual submitted capture join: '+key)
    need(observed['native_recorded_identity']==native_report['headset_serial_recorded'],'actual device/native identity')
    same_json(observed['output'],expected['output'],'actual output shape')
    native.write_output(out/'native',native_report,native_products)
    w,h=expected['output']['width'],expected['output']['height'];fmt=expected['output']['vk_format']
    literal=literal_inputs(wire,expected['output'])
    captures=[v for v in native_report['captures'] if v['capture_id']==capture_id]
    need(len(captures)==2 and [v['eye'] for v in captures]==[0,1],'complete distinct actual stereo capture')
    rows=[]
    for eye,capture in enumerate(captures):
        need((capture['frame'],capture['warp'])==(expected['frame'],expected['warp']),'capture identity')
        projection=native.strict_json(native_products[capture['projection']['path']])
        x,y=native.quality.perspective_edges(w,h,projection['fov_radians'])
        mask=native.quality.protected_mask(w,h,x,y)
        need(mask.tobytes()==native_products[capture['protected_mask']['path']],'actual central10 mask')
        raw=inputs.child(out/'reference',observed['eyes'][eye],allow_absolute=True)
        need(len(raw)==w*h*4 and observed['eyes'][eye]['channel_order']==('RGBA' if fmt==43 else 'BGRA'),'actual raw format')
        reference=raw if fmt==43 else np.frombuffer(raw,np.uint8).reshape(-1,4)[:,[2,1,0,3]].tobytes()
        candidate=native_products[capture['candidate']['path']]
        prepared=context['prepared'][eye]
        need(prepared['expected_rgba']==literal[eye],'prepared/job literal equality')
        actual_raw=context['native_raw'][eye]
        normalized=actual_raw if fmt==43 else np.frombuffer(actual_raw,np.uint8).reshape(-1,4)[:,[2,1,0,3]].tobytes()
        need(normalized==candidate,'fresh actual native raw/canonical equality')
        need(bool(np.all(prepared['mask'][mask])),'complete submitted mask retained by source/output union')
        pixels=original.pixels.compare(prepared,actual_raw,raw)
        rows.append(dict(eye=eye,frame=expected['frame'],warp=expected['warp'],**pixels,
                         original_input=expected['independent_inputs'][eye],
                         captured_submitted_sha256=native.digest(candidate),actual_reference_sha256=native.digest(raw),
                         normalized_reference_sha256=native.digest(reference),
                         reference_channel_permutation=[0,1,2,3] if fmt==43 else [2,1,0,3]))
    inputs.verify()
    for path,sha in own_sources.items():inputs.read(path,sha,1024*1024)
    exact=all(r['finite_original_source_to_submitted_exact'] for r in rows)
    report=dict(schema=1,status='pass' if exact else 'fail',evidence_integrity_verified=True,
        capture_id=capture_id,frame=expected['frame'],warp=expected['warp'],eyes=rows,
        run_hashes_sha256=run_sha,build_receipt_sha256=build_sha,application_build_receipt_sha256=application_build_sha,
        job_receipt_sha256=job_sha,replay_plan_sha256=replay_plan_sha,
        headset_physical_serial=observed['headset_physical_serial'],device=observed['device'],
        source_boundary=original.BOUNDARY,protected_radius_degrees=10,gaze_mode='fixed-center',rgb_code_tolerance=0,
        protected_mask_policy='union of full submitted central10 and proven-permuted original central10; never intersection',
        actual_original_to_submitted_rgb_exact=exact,reference_rendered_on_same_physical_headset=True,
        source_mapping_recomputed=True,job_bytes_recomputed=True,device_acquisition_recomputed=True,
        reference_geometry=expected['reference_geometry'],candidate_vbo_equivalence_assumed=False,
        actual_native_vertex_bytes_captured=False,actual_native_push_constant_bytes_captured=False,
        optical_display_equality=False,visible_artifact_assessment='not inferred; supply separate human observation',
        clean_visual_qualified=False,full_goal_qualified=False,quality_route_qualified=False,promotion_eligible=False,
        input_artifacts=inputs.reads,source_mapping_audit=expected['source_mapping_audit'],
        native_capture_audit=native_report,replay_acquisition_audit=observed,
        sources_sha256={str(p.relative_to(ROOT)):native.digest(p.read_bytes()) for p in
                       (Path(__file__),Path(original.__file__),Path(common.__file__),Path(native.__file__),Path(replay.__file__),Path(original.pixels.__file__))},
        scope='One real stereo frame: zero-tolerance original-code/reference/submitted equality; aggregate fixture/repeat/stage and human gates remain separate')
    (out/'audit.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    return report


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('run-root','build-receipt','application-build-receipt','job-receipt','replay-directory','replay-plan','runner','out'):
        p.add_argument('--'+name,type=Path,required=True)
    for name in ('run-sha','build-sha','application-build-sha','job-sha','replay-plan-sha'):p.add_argument('--'+name,required=True)
    p.add_argument('--capture-id',type=int,required=True);p.add_argument('--build-root',type=Path)
    a=p.parse_args();report=audit(a.run_root,a.run_sha,a.build_receipt,a.build_sha,a.application_build_receipt,a.application_build_sha,
                                a.capture_id,a.job_receipt,a.job_sha,a.replay_directory,a.replay_plan,a.replay_plan_sha,a.runner,a.out,a.build_root)
    print(json.dumps({k:report[k] for k in ('status','actual_original_to_submitted_rgb_exact','full_goal_qualified')}))
    raise SystemExit(0 if report['status']=='pass' else 1)

if __name__=='__main__':main()
