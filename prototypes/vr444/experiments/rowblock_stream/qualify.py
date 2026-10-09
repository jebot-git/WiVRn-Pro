#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Actual RRB bytes through VXB1 replay, then an independent native stereo oracle.

All record arrival times are synthetic logical microseconds. This program does
not open a socket or measure encoding, transport, or display performance.
"""
import argparse
import hashlib
import json
from pathlib import Path
import random
import re
import subprocess
import tempfile
import transport as t
import build

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
SID=b'vxb-test-session'
NOW=1_000_000_000
SCENARIOS=('clean','shuffle','duplicates','first_manifest_loss','first_data_loss','first_data_corrupt','first_data_truncated')

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def child(root,name):
    if not isinstance(name,str):raise ValueError('string fixture name required')
    path=(root/name).resolve()
    if not path.is_relative_to(root) or not path.is_file():raise ValueError('fixture outside declared root')
    return path

def check_build(receipt,path):
    expected={str(p.relative_to(ROOT)) for p in build.SOURCES}
    if (receipt.get('status')!='pass' or receipt.get('schema')!=1
            or receipt.get('sanitizers')!=['address','undefined']
            or set(receipt.get('sources_sha256',{}))!=expected
            or not receipt.get('compiler_dependencies')
            or not set(receipt['compiler_dependencies']).issubset(expected)):
        raise ValueError('complete sanitizer build receipt required')
    commands=receipt.get('commands',[])
    if len(commands)!=4 or any('-fsanitize=address,undefined' not in command for command in commands):
        raise ValueError('sanitizer compiler/link commands required')
    binary=Path(receipt['binary']).resolve()
    if binary.parent!=path.parent or sha(binary)!=receipt['binary_sha256']:
        raise ValueError('native binary differs from bound build')
    for name,digest in receipt['sources_sha256'].items():
        if sha(ROOT/name)!=digest:raise ValueError('compiled source differs: '+name)
    return binary

def fixture_pairs(manifest,export,corpus):
    if manifest.get('schema')!=1 or len(manifest.get('cases',[]))!=152:raise ValueError('complete 38-eye, four-block-size export required')
    indexed={};sources=set();inputs=set()
    for item in manifest['cases']:
        key=(item['source'],item['block_rows'])
        if key in indexed or item['block_rows'] not in (16,32,64,128) or item.get('exact') is not True:
            raise ValueError('duplicate or noncanonical export metadata')
        wire=child(export,item['rrb']);rgba=child(export,item['rgba']);source=child(corpus,item['source'])
        width,height,rows=item['width'],item['height'],item['block_rows'];count=t.shape(width,height,rows)
        if count!=item['blocks'] or wire.stat().st_size!=item['wire_bytes'] or rgba.stat().st_size!=width*height*4:
            raise ValueError('export shape/size differs')
        indexed[key]=item;sources.add(item['source']);inputs.update((wire,rgba,source))
    captured=[name for name in sources if not name.startswith('synthetic/')]
    synthetic=sorted(sources-set(captured))
    if len(captured)!=24 or len(synthetic)!=14:raise ValueError('capture/synthetic coverage differs')
    for source in sources:
        if {row for name,row in indexed if name==source}!={16,32,64,128}:raise ValueError('block-size coverage differs')
    profiles=[]
    for name in sorted(captured):
        match=re.fullmatch(r'(lossless|paired-a|paired-b)/([0-9]+)-([01])\.payload',name)
        if not match:raise ValueError('unknown captured eye identity')
        if match[3]!='0':continue
        partner=f'{match[1]}/{match[2]}-1.payload'
        if partner not in sources:raise ValueError('unpaired capture')
        profiles.append((f'{match[1]}/{match[2]}',name,partner,False))
    if len(profiles)!=12:raise ValueError('twelve actual stereo capture pairs required')
    profiles.extend((name.removesuffix('.payload'),name,name,True) for name in synthetic)
    result=[]
    for profile,left,right,duplicated in profiles:
        for rows in (16,32,64,128):
            eyes=(indexed[left,rows],indexed[right,rows])
            if any(eyes[0][k]!=eyes[1][k] for k in ('width','height','q','blocks')):raise ValueError('unequal stereo metadata')
            result.append((profile,rows,duplicated,eyes))
    return result,inputs

def replay(records,scenario):
    clock=[NOW];receiver=t.Receiver(SID,clock=lambda:clock[0]);receiver.issue_deadline(NOW,100_000_000,1,10)
    records=list(records)
    first=next(i for i,r in enumerate(records) if t.HEADER.unpack_from(r)[2]==t.DATA)
    if scenario=='shuffle':
        # Keep the first manifest, randomize remaining records. Arbitrary orphan
        # order above the 64 KiB orphan cap is deliberately not guaranteed.
        rest=records[1:];random.Random(19).shuffle(rest);records=records[:1]+rest
    elif scenario=='duplicates':records=[r for r in records for _ in range(2)]
    elif scenario=='first_manifest_loss':records=records[1:]
    elif scenario=='first_data_loss':del records[first]
    elif scenario=='first_data_corrupt':
        damaged=bytearray(records[first]);damaged[-1]^=1;records[first]=bytes(damaged)
    elif scenario=='first_data_truncated':records[first]=records[first][:-1]
    elif scenario!='clean':raise ValueError('unknown scenario')
    values=[]
    for record in records:
        clock[0]+=1000;value=receiver.receive(record,clock[0])
        if value:values.append(value)
        if receiver.buffered>t.MAX_BUFFERED or receiver.orphan_bytes>t.base.MAX_ORPHAN or len(receiver.pending)>2:
            raise ValueError('retained payload bound exceeded')
    success=scenario in SCENARIOS[:4]
    if len(values)!=(1 if success else 0):raise ValueError('unexpected stereo publication')
    if not success:
        receiver.expire(NOW+100_000_000)
        if receiver.pending or receiver.published:raise ValueError('incomplete candidate escaped deadline')
    return values[0] if values else None,dict(scenario=scenario,records=len(records),
        retained_payload_highwater=receiver.highwater_bytes,private_blocks_highwater=receiver.block_highwater,
        published=success)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ('manifest','export','corpus','build_receipt','out'):parser.add_argument('--'+name.replace('_','-'),type=Path,required=True)
    args=parser.parse_args();export,corpus=args.export.resolve(),args.corpus.resolve()
    receipt_path=args.build_receipt.resolve();receipt=json.loads(receipt_path.read_text());binary=check_build(receipt,receipt_path)
    manifest_path=args.manifest.resolve();manifest=json.loads(manifest_path.read_text())
    profiles,inputs=fixture_pairs(manifest,export,corpus)
    inputs.update((manifest_path,receipt_path,binary,HERE/'qualify.py',HERE/'transport.py',HERE/'test_transport.py',Path(t.base.__file__)))
    inputs.update(ROOT/name for name in receipt['sources_sha256'])
    hashes={str(path):sha(path) for path in inputs}
    results=[];native_invocations=exchanges=0;native_negative_cases=[]
    with tempfile.TemporaryDirectory(prefix='vxb-stereo-') as directory:
        tmp=Path(directory)
        for profile,rows,duplicated,eyes in profiles:
            wires=[child(export,eye['rrb']).read_bytes() for eye in eyes]
            result=dict(profile=profile,rows=rows,synthetic_duplicated_eye=duplicated,q=eyes[0]['q'],pair_bytes=sum(map(len,wires)))
            if result['pair_bytes']>t.MAX_PAIR:
                try:t.packetize(SID,1,1,1,10,NOW+100_000_000,*wires)
                except ValueError:result['status']='over_cap_rejected';results.append(result);continue
                raise ValueError('over-cap pair accepted')
            records=t.packetize(SID,1,1,1,10,NOW+100_000_000,*wires)
            scenarios=[];returned=None
            for scenario in SCENARIOS:
                pair,detail=replay(records,scenario);exchanges+=1;scenarios.append(detail)
                if pair:
                    if (pair.left,pair.right)!=tuple(wires):raise ValueError('reconstructed canonical bytes differ')
                    returned=pair
            if returned is None:raise ValueError('no exact successful pair')
            paths=(tmp/'left.rrb',tmp/'right.rrb');paths[0].write_bytes(returned.left);paths[1].write_bytes(returned.right)
            command=[str(binary),*(str(child(corpus,eye['source'])) for eye in eyes),*(str(p) for p in paths),*(str(child(export,eye['rgba'])) for eye in eyes)]
            oracle=json.loads(subprocess.check_output(command,text=True))
            if oracle.get('status')!='pass' or oracle.get('both_eyes_exact') is not True:raise ValueError('native oracle failed')
            native_invocations+=1
            if not native_negative_cases:
                for label,argument,change in (
                    ('left_rrb_corrupt',3,lambda b:b[:-1]+bytes([b[-1]^1])),
                    ('right_rrb_corrupt',4,lambda b:b[:-1]+bytes([b[-1]^1])),
                    ('right_rrb_truncated',4,lambda b:b[:-1]),
                    ('reference_shape_over_cap',1,lambda b:b[:8]+(4096).to_bytes(4,'little')*2+b[16:]),
                    ('right_rgba_oracle_corrupt',6,lambda b:bytes([b[0]^1])+b[1:])):
                    damaged=tmp/(label+'.bin');damaged.write_bytes(change(Path(command[argument]).read_bytes()))
                    negative=list(command);negative[argument]=str(damaged)
                    check=subprocess.run(negative,text=True,capture_output=True)
                    if check.returncode!=1 or check.stdout or 'Sanitizer' in check.stderr or 'runtime error:' in check.stderr:
                        raise ValueError('native malformed-input containment failed: '+label)
                    native_negative_cases.append(label)
            result.update(status='exact',records=len(records),udp_payload_bytes=sum(map(len,records)),
                declared_ipv4_bytes=sum(len(r)+28 for r in records),canonical_bytes_sha256=[hashlib.sha256(w).hexdigest() for w in wires],
                scenarios=scenarios,native_oracle=oracle)
            results.append(result)
    if any(sha(path)!=digest for path,digest in hashes.items()):raise ValueError('input/source/control/binary changed')
    report=dict(schema=1,status='pass',measurement=False,sockets_opened=False,headset_operations=False,
        capture_eyes=24,synthetic_eyes=14,actual_stereo_pairs=12,duplicated_synthetic_profiles=14,
        block_sizes=[16,32,64,128],profile_block_cases=len(results),eligible=sum(r['status']=='exact' for r in results),
        over_cap_rejected=sum(r['status']=='over_cap_rejected' for r in results),logical_replays=exchanges,
        native_asan_ubsan_stereo_oracles=native_invocations,all_inputs_unchanged=True,inputs_sha256=hashes,cases=results,
        native_negative_cases=native_negative_cases,
        limitations=['Logical record replay is not socket latency or throughput.','No incremental GPU/client decode is implemented.',
            'CRC and parity are not authentication; this protocol has no parity or retransmission.',
            'Retained pending payload bounds exclude temporary copies, published pairs, and Python metadata.',
            'The exact decoded Q8 bytes are preserved; original Q8 input is not made lossless.'])
    with args.out.open('x') as stream:json.dump(report,stream,indent=2);stream.write('\n')
    print(json.dumps({k:report[k] for k in ('status','profile_block_cases','eligible','over_cap_rejected','logical_replays','native_asan_ubsan_stereo_oracles')}))

if __name__=='__main__':main()
