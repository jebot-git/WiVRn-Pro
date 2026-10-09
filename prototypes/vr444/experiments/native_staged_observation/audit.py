#!/usr/bin/env python3
"""Audit native staged-image telemetry/readbacks; no execution or latency gate."""
import argparse,hashlib,json,math,re,statistics,struct,zlib
from pathlib import Path

def need(v,m):
    if not v:raise ValueError(m)
def sha(b):return hashlib.sha256(b).hexdigest()
def strict(b):
    def pairs(items):
        out={}
        for k,v in items:need(k not in out,'duplicate JSON key');out[k]=v
        return out
    def bad(v):raise ValueError('nonfinite JSON')
    return json.loads(b,object_pairs_hook=pairs,parse_constant=bad)
def read(p,limit=32*1024*1024):
    p=Path(p);need(p.is_file()and not p.is_symlink(),'regular file required')
    with p.open('rb')as f:b=f.read(limit+1)
    need(len(b)<=limit,'file bound');return b
def integer(v):return type(v)is int and v>=0
def number(v):return type(v)in(int,float)and math.isfinite(v)and v>=0
def relative(v):need(type(v)is str and not Path(v).is_absolute()and '..'not in Path(v).parts,'relative path');return v
def stats(values):
    x=sorted(values);need(x and all(number(v)for v in x),'finite nonempty measurements')
    return dict(count=len(x),min=x[0],median=statistics.median(x),p95_nearest_rank=x[math.ceil(.95*len(x))-1],max=x[-1])

class Bound:
    def __init__(self,root,hashes):self.root=Path(root);self.hashes=hashes;self.used={};self.cache={}
    def read(self,name,limit=32*1024*1024):
        relative(name);need(name in self.hashes,'unbound file '+name)
        if name not in self.cache:
            b=read(self.root/name,limit);need(sha(b)==self.hashes[name],'hash mismatch '+name);self.cache[name]=b;self.used[name]=sha(b)
        return self.cache[name]
    def json(self,name):return strict(self.read(name))
    def lines(self,name):return [strict(line)for line in self.read(name).splitlines()]
    def unchanged(self):need(all(sha(read(self.root/p,128*1024*1024))==h for p,h in self.used.items()),'input changed')

STAGES=('pair_decode_ms','staging_prepare_ms','submit_fence_wait_ms','queue_wait_ms','decode_upload_ms','capture_snapshot_ms')
def rows_checked(rows,eye):
    out={}
    for r in rows:
        need(type(r)is dict and all(integer(r.get(k))for k in('frame','eye','warp','rrb_generation','bytes')),'typed decoder identity')
        need(r['eye']==eye and r['frame']not in out,'eye or duplicate identity')
        need(r['effective_codec']=='rrb32'and r['rrb_output']=='staged-gpu'and r['rrb_block_rows']==32 and r['stereo_envelope_cap']==262144,'selected route')
        need(r['rrb_workers']==0 and r['configured_rrb_workers']==4 and r['rrb_generation']>0,'residual-only configuration')
        need(r['pair_decode_scope']=='stereo_once_reported_per_eye'and r['gpu_timing_valid']is False and r['staging_copy_timing_valid']is False,'timing scope')
        need(r['decode_gpu_ms']==r['cpu_decode_ms']==r['staging_copy_ms']==0,'unsupported timing field')
        need(r['capture_source']in('rrb_gpu_image_readback','not_captured')and all(number(r.get(k))for k in STAGES),'stage data')
        need((r['frame']%90==0)==(r['capture_source']=='rrb_gpu_image_readback'),'capture stride')
        if r['capture_source']=='not_captured':need(r['capture_snapshot_ms']==0,'uncaptured snapshot time')
        out[r['frame']]=r
    need(out,'decoder empty');return out

def envelope(b,frame,eye,warp,w,h):
    need(len(b)>48,'envelope too short')
    magic,kind,bw,bh,be,n,bf,bp,crc,flags=struct.unpack('<6I2Q2I',b[:48])
    need((magic,kind,bw,bh,be,bf,bp,flags)==(0x31544357,4,w,h,eye,frame,warp,0),'envelope identity/format')
    need(len(b)==48+n and zlib.crc32(b[48:])==crc,'envelope size/CRC')

def audit(run_root,manifest_sha,build_path,build_sha):
    run_root,build_path=Path(run_root),Path(build_path);manifest=read(run_root/'hashes.json');need(sha(manifest)==manifest_sha,'run manifest pin')
    build_raw=read(build_path);need(sha(build_raw)==build_sha,'build receipt pin');build_data=strict(build_raw)
    run=Bound(run_root,strict(manifest));build=Bound(build_data['source'],build_data['sha256'])
    provenance=run.json('provenance.json');summary=run.json('summary.json');cleanup=run.json('cleanup.json')
    need(provenance['rrb_output']=='staged-gpu'and provenance['custom_q']==8 and provenance['rrb_source']=='vr444','observation route')
    need(provenance['quality_capture']is False and provenance['publication_trace']is True and provenance['candidate_trace']is True,'diagnostic capture scope')
    need(cleanup['restored']is True and cleanup['cleanup_errors']==[],'cleanup failure')
    source_names=['client/decoder/test_decoder.cpp','client/decoder/staged_rowblocks_image.h','client/decoder/staged_rowblocks_image.cpp','client/decoder/staged_rowblocks_image.comp','assets/codec-test/staged-rowblocks-image.spv','common/codec_test/rrb/native.h','common/codec_test/rrb/rowblocks.hpp','common/codec_test/rrb/rowfilter.hpp','common/codec_test/wire.h']
    for name in source_names:need(sha(build.read(name))==provenance['source_helper_sha256'][name],'build/native source join')
    need(sha(build.read('assets/codec-test/staged-rowblocks-image.spv'))==provenance['shader_sha256']['staged-rowblocks-image.spv'],'shader join')
    apk=next(name for name in build.hashes if name.endswith('/wivrn-codec-tests-debug.apk'))
    need(sha(build.read(apk,128*1024*1024))==provenance['installed_apk_sha256'],'build/recorded installed APK join')
    w,h=provenance['geometry'];need(integer(w)and integer(h)and 1<=w<=4096 and 1<=h<=4096 and w*h<=4194304,'image bound')
    rows=[rows_checked(run.lines(f'client/codec-test/decode-{e}.jsonl'),e)for e in(0,1)]
    need(run.read('client/codec-test/decode-2.jsonl')==b'','unexpected alpha telemetry')
    host=[]
    for eye in(0,1):
        values={}
        for r in run.lines(f'host/encode-{eye}.jsonl'):
            need(all(integer(r.get(k))for k in('frame','eye','warp','payload_bytes'))and r['eye']==eye and r['frame']not in values,'host identity')
            need(r['effective_codec']=='rrb32'and r['quantizer']==8 and r['source_contract']=='decoded_vr444_rgb_exact','host codec/source')
            values[r['frame']]=r
        host.append(values)
    for eye in(0,1):
        for frame,r in rows[eye].items():
            need(frame in host[eye]and host[eye][frame]['warp']==r['warp']and host[eye][frame]['payload_bytes']==r['bytes'],'host/decoder join')
    paired=sorted(set(rows[0])&set(rows[1]));need(paired,'no complete decode pair')
    for frame in paired:
        a,b=rows[0][frame],rows[1][frame]
        need(a['warp']==b['warp']and a['rrb_generation']==b['rrb_generation']and a['pair_decode_ms']==b['pair_decode_ms'],'shared pair identity/time')
        need(a['bytes']+b['bytes']<=262144,'stereo envelope cap')
    presented=set()
    for r in run.lines('client/codec-test-present.jsonl'):
        need(all(integer(r.get(k))for k in('left','right','left_warp','right_warp'))and r['left']==r['right']and r['left_warp']==r['right_warp'],'present pair identity')
        frame=r['left'];need(frame in paired and rows[0][frame]['warp']==r['left_warp'],'present/decode identity')
        presented.add((frame,r['left_warp']))
    expected={f'client/codec-test/{frame}-{eye}.rgba'for eye in(0,1)for frame,r in rows[eye].items()if r['capture_source']=='rrb_gpu_image_readback'}
    actual={name for name in run.hashes if re.fullmatch(r'client/codec-test/\d+-[01]\.rgba',name)}
    need(actual==expected and actual,'readback capture population');images=[]
    for name in sorted(actual):
        frame,eye=map(int,Path(name).stem.split('-'));r=rows[eye][frame];raw=run.read(name);ref=run.read(f'host/{frame}-{eye}.reference.rgba');source=run.read(f'host/{frame}-{eye}.rgba');wire=run.read(f'host/{frame}-{eye}.envelope')
        envelope(wire,frame,eye,r['warp'],w,h);need(len(wire)==r['bytes'],'captured envelope length')
        need(len(raw)==len(ref)==len(source)==w*h*4,'capture byte size');need(raw==ref,'GPU image differs from saved codec reference')
        need(raw[3::4]==b'\xff'*(w*h),'opaque image alpha')
        images.append(dict(frame=frame,eye=eye,warp=r['warp'],source_sha256=sha(source),codec_reference_sha256=sha(ref),actual_gpu_image_sha256=sha(raw),codec_reference_exact=True,post_foveation_source_exact=raw==source,submitted_identity=(frame,r['warp'])in presented))
    generations=sorted({r['rrb_generation']for rr in rows for r in rr.values()})
    stage_report=[]
    for eye,rr in enumerate(rows):
        stage_report.append(dict(eye=eye,all_rows={k:stats([r[k]for r in rr.values()])for k in STAGES},uncaptured_rows={k:stats([r[k]for r in rr.values()if r['capture_source']=='not_captured'])for k in STAGES},captured_rows={k:stats([r[k]for r in rr.values()if r['capture_source']=='rrb_gpu_image_readback'])for k in STAGES}))
    run.unchanged();build.unchanged();need(read(run_root/'hashes.json')==manifest and read(build_path)==build_raw,'receipt changed')
    return dict(schema=1,status='pass',scope='Independent source-bound telemetry and actual decoder-image byte audit only; root trace/latency and independent scalar payload audits are separate',run=str(run_root),run_manifest_sha256=manifest_sha,build_receipt_sha256=build_sha,run_inputs=run.used,build_inputs=build.used,inputs_unchanged=True,recorded_apk_sha256=provenance['installed_apk_sha256'],geometry=[w,h],generations=generations,decoded_eyes=[len(x)for x in rows],complete_decoded_pairs=len(paired),unmatched_decoded_eyes=[len(x)-len(paired)for x in rows],presented_distinct_identities=len(presented),images=images,readback_eye_count=len(images),codec_reference_exact_eye_count=len(images),post_foveation_source_exact_eye_count=sum(x['post_foveation_source_exact']for x in images),submitted_readback_eye_count=sum(x['submitted_identity']for x in images),shared_pair_decode_once_ms=stats([rows[0][f]['pair_decode_ms']for f in paired]),per_eye_stage_ms=stage_report,root_summary_status=summary['status'],root_warm_presentation=summary['presentation']['after_warmup'],timing_scope='All-session host wall diagnostics on headset; no measured shader timestamps, no additive stage or comparative speedup inference. queue_wait precedes decode_upload; shared pair CPU appears on both eye rows and is deduplicated here.',independent_scalar_payload_decoder_reexecuted=False,submitted_image_readback=False,quality_qualified=False,latency_qualified=False,original_application_or_display_equality=False,auditor_sha256=sha(Path(__file__).read_bytes()))

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',type=Path,required=True);p.add_argument('--manifest-sha256',required=True);p.add_argument('--build-receipt',type=Path,required=True);p.add_argument('--build-receipt-sha256',required=True);p.add_argument('--out',type=Path,required=True);a=p.parse_args()
    need(not a.out.exists(),'fresh report path');r=audit(a.run,a.manifest_sha256,a.build_receipt,a.build_receipt_sha256);a.out.parent.mkdir(parents=True,exist_ok=True);a.out.write_text(json.dumps(r,indent=2)+'\n');print(json.dumps({k:r[k]for k in('status','decoded_eyes','readback_eye_count','codec_reference_exact_eye_count','post_foveation_source_exact_eye_count','quality_qualified')}))
if __name__=='__main__':main()
