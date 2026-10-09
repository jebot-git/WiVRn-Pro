#!/usr/bin/env python3
"""Recheck immutable native RRB captures; report components, never a full goal."""
import argparse
import collections
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import re

ROOT=Path(__file__).resolve().parents[4]

def need(v,m):
    if not v:raise ValueError(m)

def sha(data):return hashlib.sha256(data).hexdigest()
def json_bytes(data):
    def pairs(items):
        out={}
        for k,v in items:need(k not in out,'duplicate JSON key');out[k]=v
        return out
    def constant(x):raise ValueError('nonfinite JSON: '+x)
    return json.loads(data,object_pairs_hook=pairs,parse_constant=constant)

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class Inputs:
    def __init__(self,root,expected):
        self.root=Path(root).resolve();self.manifest=(self.root/'hashes.json').read_bytes()
        need(sha(self.manifest)==expected,'explicit run manifest hash mismatch');self.hashes=json_bytes(self.manifest)
        need(type(self.hashes) is dict and 0<len(self.hashes)<=4096,'manifest population');self.reads={}
        for name,digest in self.hashes.items():
            need(type(name) is str and not Path(name).is_absolute() and all(s not in ('','..','.') for s in name.split('/')),'unsafe manifest path')
            need(type(digest) is str and re.fullmatch('[0-9a-f]{64}',digest),'invalid digest')
    def read(self,name,limit=256*1024*1024):
        need(name in self.hashes,'unbound capture file '+name);p=self.root/name
        cursor=self.root
        for part in Path(name).parts:cursor/=part;need(not cursor.is_symlink(),'symlink capture')
        need(p.is_file() and p.stat().st_size<=limit,'artifact size/type')
        with p.open('rb') as f:data=f.read(limit+1)
        need(len(data)<=limit and sha(data)==self.hashes[name],'artifact changed '+name)
        self.reads[name]=dict(bytes=len(data),sha256=sha(data));return data
    def json(self,name):return json_bytes(self.read(name,32*1024*1024))
    def rows(self,name):return [json_bytes(s) for s in self.read(name,64*1024*1024).splitlines()]
    def verify(self):
        for name in self.hashes:self.read(name)
        need((self.root/'hashes.json').read_bytes()==self.manifest,'manifest changed')

def quantiles(values):
    need(values and all(type(x) in (int,float) and math.isfinite(x) and x>=0 for x in values),'invalid measured population')
    v=sorted(values);return dict(count=len(v),**{name:v[math.ceil(len(v)*q)-1] for name,q in [('p50',.5),('p95',.95),('p99',.99)]},max=v[-1])

def mapping(meta):
    """Diagnostic of recorded foveation span geometry; no guessed source pixels."""
    import numpy as np
    quality=load_module('rrb_report_quality',ROOT/'validation/wivrn-codecs/integration/research_quality.py')
    w,h=meta['width'],meta['height'];iw,ih=meta['encoded_input_extent'];quality.shape(w,h);quality.shape(iw,ih)
    spans=[];unit=[]
    for key,encoded,total in [('foveation_x',iw,w),('foveation_y',ih,h)]:
        values=meta[key];need(type(values) is list and len(values)%2==1 and len(values)<=4095,'foveation vector')
        need(all(type(n) is int and 0<=n<=65535 for n in values),'span counts')
        mid=len(values)//2;expanded=np.repeat(np.abs(np.arange(len(values))-mid)+1,values)
        need(len(expanded)==encoded and int(expanded.sum())==total,'span geometry')
        spans.append(expanded);unit.append(values[mid])
    fov=meta['encoded_input_fov_left_right_down_up_radians'];quality.perspective_edges(w,h,fov)
    left,right,down,up=map(math.tan,fov)
    x=left+(right-left)*np.concatenate(([0],np.cumsum(spans[0])))/w
    y=up+(down-up)*np.concatenate(([0],np.cumsum(spans[1])))/h
    mask=quality.protected_mask(iw,ih,x,y);multi=(spans[0][None,:]>1)|(spans[1][:,None]>1)
    return dict(capture_id=meta['capture_id'],frame=meta['frame'],warp=meta['warp'],eye=meta['eye'],
        reconstructed_geometry=[w,h],encoded_geometry=[iw,ih],encoded_input_fov_left_right_down_up_radians=fov,
        foveation_x=meta['foveation_x'],foveation_y=meta['foveation_y'],ratio1_encoded_run_xy=unit,
        central10_encoded_cells=int(mask.sum()),central10_cells_with_source_span_above1=int((mask&multi).sum()),
        central10_all_cells_ratio1=not bool((mask&multi).any()),mask_sha256=sha(mask.tobytes()),
        scope='Integer spans and recorded input FOV only. Assumes canonical reconstructed image axes; does not bind original app source rect/flip or prove pre-foveation pixel equality.')

def quality_space(plan,execution,provenance,application):
    need(type(plan.get('schema')) is int and plan['schema'] in (1,2),'quality plan schema')
    if plan['schema']==1:
        need('source_reference_space' not in plan,'legacy VIEW plan relabeled')
        space='VIEW'
    else:
        space=plan.get('source_reference_space')
    need(space in ('VIEW','LOCAL') and execution.get('source_application_space')==space
         and application.get('source_reference_space')==space,'quality application space join')
    need(provenance.get('scene_reference_space')==space.title(),'native application space provenance')
    need(execution.get('latency_eligible') is False,'quality profile scope')
    return space

def record(run,manifest_sha,oracle,oracle_sha,build_receipt,build_sha):
    inputs=Inputs(run,manifest_sha);inputs.verify();prov=inputs.json('provenance.json');summary=inputs.json('summary.json');cleanup=inputs.json('cleanup.json')
    need(prov.get('mode')=='custom-rrb32' and prov.get('rrb_source') in ('q1','vr444'),'RRB source provenance')
    source=prov['rrb_source'];need(prov.get('rrb_output')=='cpu' and prov.get('rrb_workers')==4 and prov.get('custom_compression')=='none','selected CPU4 contract')
    need(source!='q1' or prov.get('custom_q')==1,'Q1 request')
    need(cleanup.get('restored') is True and cleanup.get('cleanup_errors')==[],'cleanup not complete')
    receipt_raw=Path(build_receipt).read_bytes();need(sha(receipt_raw)==build_sha,'build receipt pin');build=json_bytes(receipt_raw)
    for key,path in [('server_sha256','build/codec-server/server/wivrn-pro-server'),('installed_apk_sha256','build/android/outputs/apk/debug/wivrn-codec-tests-debug.apk'),('runtime_manifest_sha256','build/codec-server/openxr_wivrn-dev.json'),('rrb_reference_binary_sha256','build/codec-server/rrb-reference')]:
        need(prov.get(key)==build['sha256'].get(path),'native binary/build join '+key)
    for name,digest in prov['source_helper_sha256'].items():need(build['sha256'].get(name)==digest,'native helper/build join '+name)
    need(prov['rrb_reference_binary_sha256']==oracle_sha,'oracle/provenance join')
    encoded={};rejected=[]
    for eye in (0,1):
        for row in inputs.rows(f'host/encode-{eye}.jsonl'):
            if row.get('type')=='rrb_pair_rejected':rejected.append(row);continue
            key=row['frame'],row['eye'];need(key not in encoded and key[1]==eye,'duplicate/wrong encoder eye')
            need(row['effective_codec']=='rrb32' and row['source_contract']==('post_foveation_rgb_exact' if source=='q1' else 'decoded_vr444_rgb_exact') and row['quantizer']==prov['custom_q'],'encoder contract')
            need(row['compression_applied'] is False and row['payload_bytes']==row['wire_payload_bytes']+48,'envelope accounting');encoded[key]=row
    need(not inputs.rows('host/encode-2.jsonl'),'opaque alpha encoder produced rows')
    decoded={};pairs=collections.defaultdict(dict);terminal={}
    for eye in (0,1):
        eye_rows=inputs.rows(f'client/codec-test/decode-{eye}.jsonl')
        for index,row in enumerate(eye_rows):
            if row.get('type')=='summary':
                need(index==len(eye_rows)-1 and eye not in terminal and row.get('pairing')=='paired','misordered decoder summary')
                terminal[eye]=row;continue
            need('type' not in row,'unknown decoder event')
            key=row['frame'],row['eye'];need(key not in decoded and key[1]==eye,'duplicate/wrong decoder eye')
            need(key in encoded and row['warp']==encoded[key]['warp'] and row['bytes']==encoded[key]['payload_bytes'],'wire/decoder identity')
            for name,value in [('effective_codec','rrb32'),('rrb_output','cpu'),('rrb_workers',4),('configured_rrb_workers',4),('rrb_vector_backend','aarch64-neon'),('cpu_vector_backend','not_used'),('cpu_output_mode','not_used'),('cpu_decode_ms',0),('pair_decode_scope','stereo_once_reported_per_eye')]:need(row.get(name)==value,'decoder telemetry '+name)
            decoded[key]=row;pairs[row['rrb_generation'],row['frame']][eye]=row
    partial=[];paired=[]
    for identity,eyes in pairs.items():
        if set(eyes)!={0,1}:partial.append(list(identity));continue
        a,b=eyes[0],eyes[1];need(a['warp']==b['warp'] and a['pair_decode_ms']==b['pair_decode_ms'] and a['bytes']+b['bytes']<=262144,'decoded stereo contract');paired.append(a['pair_decode_ms'])
    by_frame={frame:eyes for (generation,frame),eyes in pairs.items()};need(len(by_frame)==len(pairs),'ambiguous frame generation')
    present=inputs.rows('client/codec-test-present.jsonl')
    for row in present:
        need(row['left']==row['right'] and row['left_warp']==row['right_warp'],'presented stereo identity')
        eyes=by_frame.get(row['left'],{});need(set(eyes)=={0,1} and eyes[0]['warp']==row['left_warp'],'presented incomplete pair')
    encoded_pairs=collections.defaultdict(dict)
    for (frame,eye),row in encoded.items():encoded_pairs[frame][eye]=row
    for eyes in encoded_pairs.values():need(set(eyes)=={0,1} and eyes[0]['warp']==eyes[1]['warp'] and sum(r['payload_bytes'] for r in eyes.values())<=262144,'encoded stereo cap/identity')
    scalar_path=ROOT/'validation/wivrn-codecs/integration/rrb_payload_audit.py';scalar=load_module('rrb_report_scalar',scalar_path)
    scalar_result=scalar.audit(inputs.root,Path(oracle),oracle_sha,source)
    need(scalar_result['status']=='pass','scalar payload audit')
    observations=scalar_result['observations'];app=None;geometry=[]
    if prov.get('quality_application') is True:
        app_path=ROOT/'prototypes/vr444/experiments/quality_stimuli/audit.py';app_module=load_module('rrb_report_app',app_path);app=app_module.application(inputs.root/'app-source')
        execution=inputs.json('quality-application-execution.json');plan=inputs.json('quality-application-plan.json');config=inputs.json('server-config.json')
        need(sha(inputs.read('quality-application-plan.json'))==prov['quality_application_plan_sha256']==execution['original_plan_sha256'],'plan/provenance join')
        need(execution['snapshot_unchanged'] is True and execution['snapshot_sha256_after']==execution['executable_sha256']==prov['host_application_sha256']==plan['executable_sha256'],'app binary snapshot join')
        need(config['application']==execution['actual_application']==execution['application'],'actual app command join')
        quality_space(plan,execution,prov,app)
        for name in sorted(inputs.hashes):
            if re.fullmatch(r'client/codec-test/quality/capture-[1-8]-eye-[01]\.json',name):geometry.append(mapping(inputs.json(name)))
    inputs.verify()
    result=dict(schema=1,status='pass',scope='Independent native CPU RRB byte/invariant audit; preserves failed run gates',run=str(inputs.root),run_manifest_sha256=manifest_sha,build_receipt_sha256=build_sha,
        source=source,output='cpu',workers=4,encoded_stereo_pairs=len(encoded_pairs),decoded_stereo_pairs=len(pairs),partial_private_pairs=partial,presentations=len(present),
        maximum_encoded_stereo_envelope_bytes=max(sum(r['payload_bytes'] for r in eyes.values()) for eyes in encoded_pairs.values()),rejected_eye_records=len(rejected),
        shared_pair_decode_ms=quantiles(paired),pair_decode_counting='One stereo measurement, repeated identically per eye; never sum eyes',
        decoder_terminal_summaries=terminal,
        failure_counters={key:max(r[key] for r in list(decoded.values())+list(terminal.values())) for key in ('pair_failed','pair_metadata_mismatch','pair_abandoned','pair_orphan_dropped_eyes','pair_duplicate_eyes','pair_stale_eyes','pair_pending_dropped_pairs')},
        scalar_audit={k:v for k,v in scalar_result.items() if k not in ('input_sha256','observations')},
        exact_original_post_foveation_eyes=sum(o['exact_to_codec_source'] for o in observations),captured_payload_eyes=len(observations),client_reference_exact_eyes=sum(o['client_capture_present'] for o in observations),
        recorded_gates={key:{k:v for k,v in summary.get(key,{}).items() if k in ('status','errors')} for key in ('custom_evidence','research_latency_single_run','candidate_trace','source_trace','pacer_trace','publication_trace')},
        recorded_overall_status=summary['status'],warm_presentation=summary['presentation']['after_warmup'],
        application_source_audit=app,mapping_diagnostics=geometry,input_artifacts=inputs.reads,
        sources_sha256={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in [Path(__file__),scalar_path,ROOT/'validation/wivrn-codecs/integration/research_quality.py',ROOT/'prototypes/vr444/experiments/quality_stimuli/audit.py']},
        full_goal_qualified=False,quality_route_qualified=False,latency_route_qualified=False,promotion_eligible=False,
        limitations=['Post-foveation source equality is not original application-to-display equality.',
         'Native summary latency/trace verdicts are retained, not repaired or promoted by this audit.',
         'Same-render submitted-reference pixel comparison remains a separate bound stage.',
         'One chroma-grid run does not meet two-repeat/three-family/full-stage quality requirements.',
         'No optical display, human-visible artifact or new wireless-transport claim.'])
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--oracle',type=Path,required=True);p.add_argument('--oracle-sha256',required=True);p.add_argument('--build-receipt',type=Path,required=True);p.add_argument('--build-sha256',required=True);p.add_argument('--out',type=Path,required=True)
    a=p.parse_args();result=record(a.run,a.manifest_sha256,a.oracle,a.oracle_sha256,a.build_receipt,a.build_sha256)
    with a.out.open('x') as f:json.dump(result,f,indent=2);f.write('\n')
    print(json.dumps({k:result[k] for k in ('status','source','encoded_stereo_pairs','decoded_stereo_pairs','captured_payload_eyes','exact_original_post_foveation_eyes','recorded_overall_status','full_goal_qualified')}))
if __name__=='__main__':main()
