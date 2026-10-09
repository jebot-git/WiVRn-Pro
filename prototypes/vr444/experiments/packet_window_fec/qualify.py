#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual-record window repair with independent native stereo and wire comparison."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import transport as t
import loss_matrix

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
VXB=HERE.parent/'rowblock_stream'
sys.path.insert(0,str(VXB))
spec=importlib.util.spec_from_file_location('window_qualification_base',VXB/'qualify.py')
q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q);q.t=t.vxb
spec=importlib.util.spec_from_file_location('window_per_block_control',HERE.parent/'rowblock_stream_fec/transport.py')
vxp=importlib.util.module_from_spec(spec);sys.modules[spec.name]=vxp;spec.loader.exec_module(vxp)
SID=b'vwg-test-session'
NOW=1_000_000_000
SCENARIOS=('clean','shuffle','duplicates','first_manifest_loss','first_codec_source_loss',
    'first_codec_source_corrupt','first_codec_source_truncated','one_codec_source_per_group_loss',
    'all_parity_loss','codec_source_and_parity_loss','two_codec_sources_one_group_loss')

def fields(p):return t.HEADER.unpack_from(p)
def source(p):return fields(p)[2]==t.DATA
def codec_source(p):return source(p) and t.vxb.HEADER.unpack_from(p,t.HEADER.size)[2]==t.vxb.DATA

def replay(records,scenario):
    clock=[NOW];receiver=t.Receiver(SID,clock=lambda:clock[0]);receiver.issue(NOW,100_000_000,1,10)
    records=list(records);first=next(i for i,p in enumerate(records) if codec_source(p));group=fields(records[first])[5]
    grouped={}
    for i,p in enumerate(records):
        if codec_source(p):grouped.setdefault(fields(p)[5],[]).append(i)
    if scenario=='shuffle':
        rest=records[1:];random.Random(41).shuffle(rest);records=records[:1]+rest
    elif scenario=='duplicates':records=[p for p in records for _ in range(2)]
    elif scenario=='first_manifest_loss':records=records[1:]
    elif scenario=='first_codec_source_loss':del records[first]
    elif scenario=='first_codec_source_corrupt':
        p=bytearray(records[first]);p[-1]^=1;records[first]=bytes(p)
    elif scenario=='first_codec_source_truncated':records[first]=records[first][:-1]
    elif scenario=='one_codec_source_per_group_loss':
        omitted={indices[0] for indices in grouped.values()};records=[p for i,p in enumerate(records) if i not in omitted]
    elif scenario=='all_parity_loss':records=[p for p in records if source(p)]
    elif scenario=='codec_source_and_parity_loss':
        records=[p for i,p in enumerate(records) if i!=first and not(fields(p)[2]==t.PARITY and fields(p)[5]==group)]
    elif scenario=='two_codec_sources_one_group_loss':
        omitted=next((indices[:2] for indices in grouped.values() if len(indices)>=2),None)
        if omitted is None:return None,dict(scenario=scenario,applicable=False,reason='no group contains two codec DATA records')
        records=[p for i,p in enumerate(records) if i not in omitted]
    elif scenario!='clean':raise ValueError('unknown scenario')
    values=[]
    for record in records:
        clock[0]+=1000;value=receiver.receive(record,clock[0])
        if value:values.append(value)
        if receiver.buffered>t.MAX_BUFFERED or receiver.inner.orphan_bytes>t.base.MAX_ORPHAN or len(receiver.frames)>2:
            raise ValueError('retained payload bound exceeded')
    success=scenario not in SCENARIOS[-2:]
    if len(values)!=(1 if success else 0):raise ValueError('unexpected stereo publication: '+scenario)
    if not success:
        receiver.expire(NOW+100_000_000)
        if receiver.frames or receiver.inner.pending or receiver.published:raise ValueError('incomplete frame escaped deadline')
    if scenario in SCENARIOS[4:8] and receiver.stats['recovered_record']<1:raise ValueError('required recovery was not exercised')
    return values[0] if values else None,dict(scenario=scenario,applicable=True,records=len(records),
        retained_payload_highwater=receiver.highwater,recovered_records=receiver.stats['recovered_record'],published=success)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','export','corpus','build_receipt','out'):parser.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    args=parser.parse_args();export,corpus=args.export.resolve(),args.corpus.resolve()
    receipt_path=args.build_receipt.resolve();receipt=json.loads(receipt_path.read_text());binary=q.check_build(receipt,receipt_path)
    manifest_path=args.manifest.resolve();profiles,inputs=q.fixture_pairs(json.loads(manifest_path.read_text()),export,corpus)
    inputs.update((manifest_path,receipt_path,binary,HERE/'qualify.py',HERE/'transport.py',HERE/'test_transport.py',
        Path(t.base.__file__),Path(t.vxb.__file__),Path(vxp.__file__),Path(q.__file__),Path(loss_matrix.__file__)))
    inputs.update(ROOT/name for name in receipt['sources_sha256']);hashes={str(p):q.sha(p) for p in inputs}
    results=[];invocations=exchanges=0
    with tempfile.TemporaryDirectory(prefix='vwg-stereo-') as directory:
        tmp=Path(directory)
        for profile,rows,duplicated,eyes in profiles:
            wires=[q.child(export,eye['rrb']).read_bytes() for eye in eyes]
            for k in (4,8,16):
                result=dict(profile=profile,rows=rows,k=k,synthetic_duplicated_eye=duplicated,q=eyes[0]['q'],pair_bytes=sum(map(len,wires)))
                if result['pair_bytes']>t.vxb.MAX_PAIR:
                    try:t.packetize(1,SID,1,1,1,10,NOW+100_000_000,*wires,k=k)
                    except ValueError:result['status']='over_cap_rejected';results.append(result);continue
                    raise ValueError('over-cap pair accepted')
                records=t.packetize(1,SID,1,1,1,10,NOW+100_000_000,*wires,k=k)
                scenarios=[];returned=None
                for scenario in SCENARIOS:
                    pair,detail=replay(records,scenario);exchanges+=int(detail['applicable']);scenarios.append(detail)
                    if pair:
                        if (pair.left,pair.right)!=tuple(wires):raise ValueError('canonical stereo differs')
                        returned=pair
                if returned is None:raise ValueError('no successful exact pair')
                paths=(tmp/'left.rrb',tmp/'right.rrb');paths[0].write_bytes(returned.left);paths[1].write_bytes(returned.right)
                command=[str(binary),*(str(q.child(corpus,eye['source'])) for eye in eyes),*(str(p) for p in paths),*(str(q.child(export,eye['rgba'])) for eye in eyes)]
                oracle=json.loads(subprocess.check_output(command,text=True))
                if oracle.get('status')!='pass' or oracle.get('both_eyes_exact') is not True:raise ValueError('native oracle failed')
                invocations+=1
                nofec=t.vxb.packetize(SID,1,1,1,10,NOW+100_000_000,*wires)
                block=vxp.packetize(SID,1,1,1,10,NOW+100_000_000,*wires,k=k)
                matched_erasures=loss_matrix.study(records,block,vxp,SID,*wires)
                result.update(status='exact',records=len(records),udp_payload_bytes=sum(map(len,records)),
                    source_records=sum(source(p) for p in records),parity_records=sum(not source(p) for p in records),
                    source_wrapper_bytes=sum(source(p) for p in records)*t.HEADER.size,
                    parity_udp_bytes=sum(len(p) for p in records if not source(p)),
                    nofec_udp_payload_bytes=sum(map(len,nofec)),per_block_fec_udp_payload_bytes=sum(map(len,block)),
                    declared_ipv4_bytes=sum(len(p)+28 for p in records),
                    canonical_bytes_sha256=[hashlib.sha256(w).hexdigest() for w in wires],scenarios=scenarios,
                    matched_codec_erasure_cases=matched_erasures,native_oracle=oracle)
                results.append(result)
    if any(q.sha(path)!=digest for path,digest in hashes.items()):raise ValueError('source/input/control/binary changed')
    report=dict(schema=1,status='pass',measurement=False,sockets_opened=False,headset_operations=False,
        capture_eyes=24,synthetic_eyes=14,actual_stereo_pairs=12,duplicated_synthetic_profiles=14,
        block_sizes=[16,32,64,128],groups=[4,8,16],profile_block_fec_cases=len(results),
        eligible=sum(r['status']=='exact' for r in results),over_cap_rejected=sum(r['status']=='over_cap_rejected' for r in results),
        logical_replays=exchanges,native_asan_ubsan_stereo_oracles=invocations,
        matched_erasure_replays=2*sum(len(r.get('matched_codec_erasure_cases',[])) for r in results),
        all_inputs_unchanged=True,inputs_sha256=hashes,cases=results,
        limitations=['Logical replay, not network/performance evidence.','New compact handle negotiation/lifetime is prototype-only and unauthenticated.',
            'One missing source per group is recoverable; two codec DATA losses in one group withhold stereo.',
            'Larger groups can delay repair and change burst sensitivity; byte reductions alone do not establish latency.',
            'Matched erased codec-fragment IDs keep all parity and manifests. These are not equal contiguous wire bursts or loss probabilities.',
            'Bounds describe retained payload, excluding temporary and previously published copies/metadata.',
            'No incremental GPU reconstruction or measured pipeline overlap.'])
    with args.out.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({k:report[k] for k in ('status','profile_block_fec_cases','eligible','over_cap_rejected','logical_replays','native_asan_ubsan_stereo_oracles')}))

if __name__=='__main__':main()
