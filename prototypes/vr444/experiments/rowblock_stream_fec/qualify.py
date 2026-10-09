#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Qualify exact per-block repair using actual bytes and the native stereo oracle."""
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

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
VXB=HERE.parent/'rowblock_stream'
sys.path.insert(0,str(VXB))
spec=importlib.util.spec_from_file_location('vxp_qualification_base',VXB/'qualify.py')
q=importlib.util.module_from_spec(spec);spec.loader.exec_module(q)
SID=b'vxp-test-session'
NOW=1_000_000_000
SCENARIOS=('clean','shuffle','duplicates','first_manifest_loss','first_data_loss',
    'first_data_corrupt','first_data_truncated','one_source_per_group_loss','all_parity_loss',
    'first_source_and_parity_loss','two_sources_one_group_loss')

def fields(record):return t.HEADER.unpack_from(record)

def replay(records,scenario):
    clock=[NOW];receiver=t.Receiver(SID,clock=lambda:clock[0]);receiver.issue_deadline(NOW,100_000_000,1,10)
    records=list(records);first=next(i for i,p in enumerate(records) if fields(p)[2]==t.DATA)
    head=fields(records[first]);key=head[3],head[9],head[11]//head[4]
    if scenario=='shuffle':
        rest=records[1:];random.Random(31).shuffle(rest);records=records[:1]+rest
    elif scenario=='duplicates':records=[p for p in records for _ in range(2)]
    elif scenario=='first_manifest_loss':records=records[1:]
    elif scenario=='first_data_loss':del records[first]
    elif scenario=='first_data_corrupt':
        body=bytearray(records[first]);body[-1]^=1;records[first]=bytes(body)
    elif scenario=='first_data_truncated':records[first]=records[first][:-1]
    elif scenario=='one_source_per_group_loss':records=[p for p in records if fields(p)[2]!=t.DATA or fields(p)[11]%fields(p)[4]!=0]
    elif scenario=='all_parity_loss':records=[p for p in records if fields(p)[2]!=t.PARITY]
    elif scenario=='first_source_and_parity_loss':
        records=[p for i,p in enumerate(records) if i!=first and not(fields(p)[2]==t.PARITY and (fields(p)[3],fields(p)[9],fields(p)[11])==key)]
    elif scenario=='two_sources_one_group_loss':
        source=next((fields(p) for p in records if fields(p)[2]==t.DATA and fields(p)[12]>=2),None)
        if source is None:return None,dict(scenario=scenario,applicable=False,reason='all blocks have one source shard')
        records=[p for p in records if not(fields(p)[2]==t.DATA and fields(p)[3]==source[3] and fields(p)[9]==source[9] and fields(p)[11] in (0,1))]
    elif scenario!='clean':raise ValueError('unknown scenario')
    values=[]
    for record in records:
        clock[0]+=1000;value=receiver.receive(record,clock[0])
        if value:values.append(value)
        if receiver.buffered>t.MAX_BUFFERED or receiver.orphan_bytes>t.base.MAX_ORPHAN or len(receiver.pending)>2:
            raise ValueError('retained payload bound exceeded')
    success=scenario not in SCENARIOS[-2:]
    if len(values)!=(1 if success else 0):raise ValueError('unexpected stereo publication: '+scenario)
    if not success:
        receiver.expire(NOW+100_000_000)
        if receiver.pending or receiver.published:raise ValueError('incomplete candidate escaped deadline')
    if scenario in ('first_data_loss','first_data_corrupt','first_data_truncated','one_source_per_group_loss') and receiver.stats['recovered_private_fragment']<1:
        raise ValueError('expected repair was not exercised')
    return values[0] if values else None,dict(scenario=scenario,applicable=True,records=len(records),
        retained_payload_highwater=receiver.highwater_bytes,recovered_fragments=receiver.stats['recovered_private_fragment'],
        private_blocks_highwater=receiver.block_highwater,published=success)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','export','corpus','build_receipt','out'):parser.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    args=parser.parse_args();export,corpus=args.export.resolve(),args.corpus.resolve()
    receipt_path=args.build_receipt.resolve();receipt=json.loads(receipt_path.read_text());binary=q.check_build(receipt,receipt_path)
    manifest_path=args.manifest.resolve();profiles,inputs=q.fixture_pairs(json.loads(manifest_path.read_text()),export,corpus)
    inputs.update((manifest_path,receipt_path,binary,HERE/'qualify.py',HERE/'transport.py',HERE/'test_transport.py',
        Path(t.base.__file__),Path(t.vxb.__file__),Path(q.__file__)))
    inputs.update(ROOT/name for name in receipt['sources_sha256'])
    hashes={str(path):q.sha(path) for path in inputs}
    results=[];native_invocations=exchanges=0
    with tempfile.TemporaryDirectory(prefix='vxp-stereo-') as directory:
        tmp=Path(directory)
        for profile,rows,duplicated,eyes in profiles:
            wires=[q.child(export,eye['rrb']).read_bytes() for eye in eyes]
            for k in (4,8,16):
                result=dict(profile=profile,rows=rows,k=k,synthetic_duplicated_eye=duplicated,q=eyes[0]['q'],pair_bytes=sum(map(len,wires)))
                if result['pair_bytes']>t.MAX_PAIR:
                    try:t.packetize(SID,1,1,1,10,NOW+100_000_000,*wires,k=k)
                    except ValueError:result['status']='over_cap_rejected';results.append(result);continue
                    raise ValueError('over-cap pair accepted')
                records=t.packetize(SID,1,1,1,10,NOW+100_000_000,*wires,k=k)
                scenarios=[];returned=None
                for scenario in SCENARIOS:
                    pair,detail=replay(records,scenario);exchanges+=int(detail['applicable']);scenarios.append(detail)
                    if pair:
                        if (pair.left,pair.right)!=tuple(wires):raise ValueError('reconstructed canonical bytes differ')
                        returned=pair
                if returned is None:raise ValueError('no exact successful pair')
                paths=(tmp/'left.rrb',tmp/'right.rrb');paths[0].write_bytes(returned.left);paths[1].write_bytes(returned.right)
                command=[str(binary),*(str(q.child(corpus,eye['source'])) for eye in eyes),*(str(p) for p in paths),*(str(q.child(export,eye['rgba'])) for eye in eyes)]
                oracle=json.loads(subprocess.check_output(command,text=True))
                if oracle.get('status')!='pass' or oracle.get('both_eyes_exact') is not True:raise ValueError('native oracle failed')
                native_invocations+=1
                nofec=t.vxb.packetize(SID,1,1,1,10,NOW+100_000_000,*wires)
                result.update(status='exact',records=len(records),udp_payload_bytes=sum(map(len,records)),
                    nofec_udp_payload_bytes=sum(map(len,nofec)),parity_records=sum(fields(p)[2]==t.PARITY for p in records),
                    parity_udp_bytes=sum(len(p) for p in records if fields(p)[2]==t.PARITY),
                    declared_ipv4_bytes=sum(len(p)+28 for p in records),
                    canonical_bytes_sha256=[hashlib.sha256(w).hexdigest() for w in wires],scenarios=scenarios,native_oracle=oracle)
                results.append(result)
    if any(q.sha(path)!=digest for path,digest in hashes.items()):raise ValueError('input/source/control/binary changed')
    report=dict(schema=1,status='pass',measurement=False,sockets_opened=False,headset_operations=False,
        capture_eyes=24,synthetic_eyes=14,actual_stereo_pairs=12,duplicated_synthetic_profiles=14,
        block_sizes=[16,32,64,128],groups=[4,8,16],profile_block_fec_cases=len(results),
        eligible=sum(r['status']=='exact' for r in results),over_cap_rejected=sum(r['status']=='over_cap_rejected' for r in results),
        logical_replays=exchanges,native_asan_ubsan_stereo_oracles=native_invocations,
        all_inputs_unchanged=True,inputs_sha256=hashes,cases=results,
        limitations=['Logical replay, not a socket/performance experiment.','CRC/XOR are not authentication.',
            'One missing source per independent XOR group is recoverable; two in the same group are not.',
            'Retained payload excludes transient/published copies and metadata.','No incremental GPU reconstruction or measured overlap.'])
    with args.out.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({k:report[k] for k in ('status','profile_block_fec_cases','eligible','over_cap_rejected','logical_replays','native_asan_ubsan_stereo_oracles')}))

if __name__=='__main__':main()
