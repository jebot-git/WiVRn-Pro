#!/usr/bin/env python3
"""Offline diagnostic only: bind original failed run and later recovery separately."""
import argparse, hashlib, importlib.util, io, json, math, pathlib, re, sys, tarfile, tempfile
import numpy as np
ROOT=pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0,str(ROOT/'validation/wivrn-codecs/integration'))
import original_source_pixels as pixels
import rrb_payload_audit as payload
m=pixels.mapping;c=m.capture;need=c.require

def same(a,b):return json.dumps(a,sort_keys=True,allow_nan=False)==json.dumps(b,sort_keys=True,allow_nan=False)
def stats(values):
    need(values and all(type(v)in(int,float)and math.isfinite(v)and v>=0 for v in values),'finite nonnegative cost population')
    v=sorted(values);return dict(count=len(v),p50=v[math.ceil(.5*len(v))-1],p95=v[math.ceil(.95*len(v))-1],maximum=v[-1],method='nearest-rank')

class Inputs:
    def __init__(self):self.reads={}
    def read(self,path,sha=None,limit=64*1024*1024):
        path=pathlib.Path(path);need(path.is_file()and not path.is_symlink()and path.stat().st_size<=limit,'bounded ordinary input')
        raw=path.read_bytes();need(len(raw)<=limit,'bounded input read');h=c.digest(raw)
        need(sha is None or h==sha,'pinned artifact changed: '+str(path))
        need(str(path)not in self.reads or self.reads[str(path)]['sha256']==h,'input changed during analysis')
        self.reads[str(path)]={'sha256':h,'bytes':len(raw)};return raw
    def json(self,path,sha=None):return c.strict_json(self.read(path,sha))
    def verify(self):
        for path,d in list(self.reads.items()):self.read(path,d['sha256'],max(d['bytes'],1))

def confined(root,name):
    p=pathlib.PurePosixPath(name)
    need(type(name)is str and not p.is_absolute() and str(p)==name and all(v not in('..','.')for v in p.parts),'canonical relative artifact')
    out=root/p;need(out.resolve().is_relative_to(root.resolve())and not any(part.is_symlink()for part in(out,*out.parents)if part!=root.parent),'confined ordinary artifact')
    return out

def bind_inventory(inputs,root,manifest,*,exact=False):
    need(type(manifest)is dict and manifest,'nonempty hash inventory')
    if exact:need({str(p.relative_to(root))for p in(root/'client').rglob('*')if p.is_file()}==set(manifest),'recovery population changed')
    for name,sha in manifest.items():need(c.valid_sha(sha),'artifact SHA');inputs.read(confined(root,name),sha,512*1024*1024)

def decoder_costs(encoded,decoded):
    enc={};dec={}
    for eye in(0,1):
        for row in encoded[eye]:
            need(type(row.get('frame'))is int and row['eye']==eye and row['effective_codec']=='rrb32'and row['quantizer']==1,'encoded contract')
            key=(row['frame'],eye);need(key not in enc,'duplicate encoder row');enc[key]=row
        for row in decoded[eye]:
            if row.get('type')=='summary':continue
            key=(row['frame'],eye);need(key not in dec and key in enc,'decoder identity not uniquely encoded')
            need(type(row['frame'])is int and row['eye']==eye and row['warp']==enc[key]['warp'],'decode frame/warp join')
            need(row['effective_codec']=='rrb32'and row['rrb_output']=='cpu'and row['rrb_workers']==row['configured_rrb_workers']==4
                 and row['rrb_vector_backend']=='aarch64-neon'and row['pair_decode_scope']=='stereo_once_reported_per_eye','actual CPU4 implementation')
            dec[key]=row
    frames=sorted({f for f,e in dec});shared=[]
    for f in frames:
        need((f,0)in dec and(f,1)in dec,'partial decode pair')
        a,b=dec[f,0],dec[f,1]
        need(all(a[k]==b[k]for k in('warp','rrb_generation','pair_decode_scope','pair_decode_ms')),'shared stereo decoder timing mismatch')
        shared.append(a['pair_decode_ms'])
    timing=dict(shared_pair_decode_ms=stats(shared))
    for key in('staging_copy_ms','submit_fence_wait_ms','decode_upload_ms','queue_wait_ms','capture_snapshot_ms'):
        timing[key]={str(e):stats([dec[f,e][key]for f in frames])for e in(0,1)}
    timing['host_encode_cpu_wall_ms']={str(e):stats([row['encode_cpu_wall_ms']for(f,eye),row in enc.items()if eye==e])for e in(0,1)}
    timing['scope']='All recorded startup/quality-capture samples, no latency qualification. Shared pair decode counted once; eye upload/copy/wait clocks overlap and are not additive. Host/client clocks are never subtracted.'
    return dec,timing

def audit(plan):
    i=Inputs();run=pathlib.Path(plan['original_run']);recovery=pathlib.Path(plan['recovery'])
    for path in (pathlib.Path(__file__),pathlib.Path(pixels.__file__),pathlib.Path(payload.__file__),pathlib.Path(m.__file__),pathlib.Path(c.__file__),pathlib.Path(c.quality.__file__)):
        i.read(path)
    manifest=i.json(run/'hashes.json',plan['original_hashes_sha256']);bind_inventory(i,run,manifest)
    rec=i.json(recovery/'recovery.json',plan['recovery_receipt_sha256']);need(rec['status']=='recovered'and rec['returncode']==0 and not rec['stderr'],'successful diagnostic recovery')
    bind_inventory(i,recovery,rec['files'],exact=True)
    archive=i.read(recovery/'client.tar.gz',rec['archive_sha256']);need(len(archive)==rec['archive_bytes'],'recovery archive size')
    # Independently bind every extracted product to the compressed recovery bytes.
    seen=set()
    with tarfile.open(fileobj=io.BytesIO(archive),mode='r:gz')as tar:
        for member in tar:
            if member.isdir():continue
            need(member.isfile()and not member.issym()and not member.islnk(),'archive non-file entry')
            name='client/'+member.name;confined(recovery,name);need(name in rec['files']and name not in seen and member.size<=16*1024*1024,'archive inventory')
            raw=tar.extractfile(member).read();need(c.digest(raw)==rec['files'][name],'archive/extracted bytes differ');seen.add(name)
    need(seen==set(rec['files']),'archive omitted products')
    diagnostic=i.json(recovery/'diagnostic-summary.json',plan['diagnostic_summary_sha256'])
    need(diagnostic['original_hashes_sha256']==plan['original_hashes_sha256']and diagnostic['recovery_receipt_sha256']==plan['recovery_receipt_sha256']and diagnostic['recovery_qualifies_original_failed_run']is False,'recovery/original chain')
    summary=c.strict_json(i.read(run/'summary.json',manifest['summary.json']));need(summary['status']=='fail','original failed acquisition must stay failed')
    need(c.digest(i.read(run/'summary.json'))==diagnostic['original_summary_sha256']and c.digest(i.read(run/'cleanup.json'))==diagnostic['original_cleanup_sha256'],'original failure bindings')
    prov=c.strict_json(i.read(run/'provenance.json'));need(prov['rrb_source']=='q1'and prov['rrb_workers']==4 and prov['rrb_output']=='cpu','explicit actual candidate')
    build=i.json(plan['build_receipt'],plan['build_sha256']);need(plan['build_sha256']==prov['source_mapping_build_receipt_sha256'],'native build/run join')
    oracle_info=build['build_results']['oracle'];oracle=i.json(oracle_info['path'],oracle_info['sha256']);need(same(oracle,oracle_info['result'])and oracle['returncode']==0 and oracle['changed_inputs']==[],'scalar build receipt')
    for path,sha in oracle['source_sha256'].items():i.read(path,sha)
    reference=pathlib.Path(build['source'])/'build/codec-server/rrb-reference';reference_sha=build['sha256']['build/codec-server/rrb-reference']
    need(reference_sha==oracle['binary_sha256']==prov['rrb_reference_binary_sha256'],'actual scalar executable join');i.read(reference,reference_sha)
    def lines(root,name,inventory):return[c.strict_json(v)for v in i.read(root/name,inventory[name]).splitlines()]
    enc=[lines(run,f'host/encode-{e}.jsonl',manifest)for e in(0,1)]
    dec=[lines(recovery,f'client/codec-test/decode-{e}.jsonl',rec['files'])for e in(0,1)]
    decoded,timing=decoder_costs(enc,dec)
    # Join separately pinned sources into a private local snapshot ONLY for the
    # existing scalar audit. This snapshot is not a repaired native acquisition.
    with tempfile.TemporaryDirectory(prefix='rrb-recovery-diagnostic-')as temp:
        tmp=pathlib.Path(temp);names=[n for n in manifest if re.fullmatch(r'host/[0-9]+-[01]\.(?:payload|envelope|rgba|reference\.rgba)',n)]
        for n in names:
            out=tmp/n;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(i.read(run/n,manifest[n]))
        for n in rec['files']:
            if n=='client/codec-test-present.jsonl'or re.fullmatch(r'client/codec-test/[0-9]+-[01]\.rgba',n):
                out=tmp/n;out.parent.mkdir(parents=True,exist_ok=True);out.write_bytes(i.read(recovery/n,rec['files'][n]))
        scalar=payload.audit(tmp,reference,reference_sha,'q1')
        scalar['input_sha256']={str(pathlib.Path(k).relative_to(tmp)):v for k,v in scalar['input_sha256'].items()}
    need(scalar['captured_eye_payloads']==30 and all(v['client_capture_present']for v in scalar['observations']),'complete30 recovered payload eyes')
    for row in scalar['observations']:
        key=(row['frame'],row['eye']);need(key in decoded,'captured pixels missing actual decode telemetry')
        info=payload.envelope(i.read(run/f'host/{key[0]}-{key[1]}.envelope'),i.read(run/f'host/{key[0]}-{key[1]}.payload'),*key)
        need(info['warp']==decoded[key]['warp'],'captured payload/decoder warp join')
    records=m.records(lines(run,m.TRACE,manifest));joined,_=m.join(records,lines(run,'app-source/app.jsonl',manifest),lines(run,'host/source-trace.jsonl',manifest))
    source=[i.read(run/f'app-source/eye{e}.source.rgba')for e in(0,1)];pixel_rows=[]
    for cid in(1,2,3):
        for eye in(0,1):
            stem=f'client/codec-test/quality/capture-{cid}-eye-{eye}'
            meta=c.strict_json(i.read(recovery/(stem+'.json')));record=next(r for r in records if r['frame']==meta['frame'])
            prepared=pixels.prepare(record,eye,source[eye],meta,joined[record['frame']]['application']['views'][eye],encoded_rgba=i.read(run/f'host/{record["frame"]}-{eye}.rgba'))
            raw=i.read(recovery/(stem+'.raw'));canonical=pixels._rgba(raw,meta['width'],meta['height'],meta['vk_format'])
            comparison=c.quality.compare_rgba(prepared['expected_rgba'],canonical,meta['width'],meta['height'],prepared['mask'])
            a=np.frombuffer(prepared['expected_rgba'],np.uint8).reshape(meta['height'],meta['width'],4);b=np.frombuffer(canonical,np.uint8).reshape(a.shape)
            delta=np.abs(a[:,:,:3].astype(np.int16)-b[:,:,:3].astype(np.int16));changed=np.any(delta!=0,axis=2)&prepared['mask'];yy,xx=np.nonzero(changed)
            comparison.update(protected_alpha_mismatched_pixels=int((a[:,:,3][prepared['mask']]!=b[:,:,3][prepared['mask']]).sum()),
                changed_bbox_xyxy=[int(xx.min()),int(yy.min()),int(xx.max()),int(yy.max())]if len(xx)else None,
                changed_samples=[dict(x=int(x),y=int(y),expected=a[y,x,:3].tolist(),actual=b[y,x,:3].tolist())for y,x in zip(yy[:12],xx[:12])])
            pixel_rows.append(dict(capture_id=cid,eye=eye,frame=meta['frame'],warp=meta['warp'],proof=prepared['proof'],native_to_original=comparison,replay_performed=False))
    i.verify()
    return dict(schema=1,status='diagnostic_only',original_acquisition_status=summary['status'],original_acquisition_requalified=False,
        manifests=dict(original_hashes_sha256=plan['original_hashes_sha256'],recovery_receipt_sha256=plan['recovery_receipt_sha256'],diagnostic_summary_sha256=plan['diagnostic_summary_sha256'],build_receipt_sha256=plan['build_sha256']),
        scalar_payload=scalar,complete_actual_decoded_pairs=len(decoded)//2,decoder_summaries=[v[-1]for v in dec],costs=timing,
        native_to_literal_original=pixel_rows,all_protected_native_rgb_exact=all(r['native_to_original']['protected_exact']for r in pixel_rows),
        input_artifacts=i.reads,inputs_unchanged=True,full_goal_qualified=False,quality_route_qualified=False,latency_qualified=False,device_accessed=False,
        limits=['Later recovered client bytes are separately pinned; original failed archive transfer remains failed.',
        'Native-to-original is a necessary pixel condition; no same-device identity reference is fabricated.',
        'No whole acquired-run certification, optical inference, production performance or cross-clock arithmetic.',
        'Existing build/scalar receipts bind declared sources and executable; no rebuild is performed.'])

def main():
    p=argparse.ArgumentParser();p.add_argument('plan',type=pathlib.Path);p.add_argument('output',type=pathlib.Path);a=p.parse_args()
    need(not a.output.exists(),'fresh report output');plan_raw=a.plan.read_bytes();plan=c.strict_json(plan_raw);report=audit(plan)
    need(a.plan.read_bytes()==plan_raw,'plan changed during audit')
    report['plan_sha256']=c.digest(plan_raw);report['auditor_sha256']=c.digest(pathlib.Path(__file__).read_bytes())
    a.output.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n');print(json.dumps({k:report[k]for k in('status','complete_actual_decoded_pairs','all_protected_native_rgb_exact','full_goal_qualified')}))
if __name__=='__main__':main()
