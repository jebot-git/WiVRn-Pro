#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Finite seeded VWG grammar/lifecycle campaign; no sockets or timing claims."""
import argparse
from collections import Counter
import hashlib
import importlib.util
import itertools
import json
from pathlib import Path
import random
import struct
import subprocess
import sys

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[3]
PIN='788ba8b4c1bfb931c17d7a2ae9ad237c5ed48afc'
spec=importlib.util.spec_from_file_location('campaign_vwg',HERE.parent/'packet_window_fec/transport.py')
t=importlib.util.module_from_spec(spec);sys.modules[spec.name]=t;spec.loader.exec_module(t)
SID=b'window-campaign!'
NOW=1_000_000_000
DIMENSIONS=((1,1,16),(17,33,16),(31,65,32),(64,17,16))
MUTATORS=('bit','truncate','extend','outer_kind','outer_k','outer_slot','outer_group',
    'outer_count','outer_handle','outer_version','outer_magic','inner_eye','inner_reserved',
    'inner_frame','inner_length','inner_shards','inner_block_crc','inner_body',
    'parity_body','parity_overlong')

def need(ok,message):
    if not ok:raise ValueError(message)

def sha(raw):return hashlib.sha256(raw).hexdigest()

def fixture(frame,width,height,rows):
    eyes=[]
    for eye in (0,1):
        blocks=[]
        for index,y in enumerate(range(0,height,rows)):
            count=min(rows,height-y);body=bytes((i+frame*17+eye*71+index*11)%256 for i in range(width*count*3))
            header=bytearray(32);header[:4]=b'RRF1';header[4]=1
            struct.pack_into('<IIIII',header,8,width,count,len(body),len(body),t.crc(body))
            struct.pack_into('<I',header,28,t.crc(header[:28]));blocks.append(bytes(header)+body)
        eyes.append(t.vxb.assemble(width,height,rows,blocks))
    return tuple(eyes)

def fields(record):return list(t.HEADER.unpack_from(record))
def is_source(record):return fields(record)[2]==t.DATA
def is_codec(record):return is_source(record) and t.vxb.HEADER.unpack_from(record,t.HEADER.size)[2]==t.vxb.DATA

def outer_reseal(record):
    return bytes(record[:20]+struct.pack('<I',t.crc(record[:20]+record[24:]))+record[24:])

def mutate(record,kind,rng):
    raw=bytearray(record);outer=fields(raw)
    if kind=='bit':raw[rng.randrange(len(raw))]^=1<<rng.randrange(8);return bytes(raw)
    if kind=='truncate':return bytes(raw[:rng.randrange(len(raw))])
    if kind=='extend':return bytes(raw)+bytes(rng.randrange(1,16))
    if kind.startswith('outer_'):
        change={'kind':(2,3),'k':(3,32),'slot':(4,255),'group':(5,t.MAX_RECORDS),
            'count':(6,outer[3]+1),'handle':(7,2**63),'version':(1,2),'magic':(0,b'BAD!')}
        index,value=change[kind[6:]];outer[index]=value
        return outer_reseal(t.HEADER.pack(*outer)+raw[t.HEADER.size:])
    if kind.startswith('parity_'):
        need(not is_source(raw),'parity mutator needs parity')
        if kind=='parity_overlong':raw+=b'\0'
        else:raw[t.HEADER.size+rng.randrange(len(raw)-t.HEADER.size)]^=1
        return outer_reseal(raw)
    need(is_codec(raw),'inner mutator needs codec DATA')
    inner=list(t.vxb.HEADER.unpack_from(raw,t.HEADER.size));body=raw[t.HEADER.size+t.vxb.HEADER.size:]
    if kind=='inner_body':body[rng.randrange(len(body))]^=1
    else:
        change={'eye':(3,2),'reserved':(16,1),'frame':(7,inner[7]+100),
            'length':(15,inner[15]+1),'shards':(12,0),'block_crc':(14,inner[14]^1)}
        index,value=change[kind[6:]];inner[index]=value
    header=t.vxb.HEADER.pack(*inner)
    inner_record=header[:52]+struct.pack('<I',t.crc(header[:52]+body))+body
    return outer_reseal(raw[:t.HEADER.size]+inner_record)

class Replay:
    def __init__(self):
        self.clock=NOW;self.receiver=t.Receiver(SID,clock=lambda:self.clock)
        self.expected={};self.results=[];self.last=0;self.highwater=0
    def issue(self,frame,k,dims):
        wire=fixture(frame,*dims);handle,deadline=self.receiver.issue(self.clock,NOW+100_000_000-self.clock,frame,frame+10)
        self.expected[frame]=wire
        return t.packetize(handle,SID,1,1,frame,frame+10,deadline,*wire,k=k)
    def feed(self,record):
        self.clock+=1000;pair=self.receiver.receive(record,self.clock)
        if pair is not None:
            need(pair.frame in self.expected and pair.frame>self.last,'unknown/repeated/regressing published frame')
            need(pair.warp==pair.frame+10,'published warp changed')
            need((pair.left,pair.right)==self.expected[pair.frame],'published canonical stereo changed')
            self.results.append(pair.frame);self.last=pair.frame
        receiver=self.receiver
        need(receiver.buffered<=t.MAX_BUFFERED and len(receiver.frames)<=2 and len(receiver.tickets)<=32,'wrapper bound')
        need(receiver.inner.orphan_bytes<=t.base.MAX_ORPHAN and len(receiver.inner.pending)<=2,'inner bound')
        need(set(receiver.frames)<=set(receiver.tickets),'state without issued ticket')
        for state in receiver.frames.values():
            limit=(t.MAX_RECORDS+state.k-1)//state.k
            need(state.bytes<=t.MAX_WINDOW_BYTES and len(state.done)+len(state.groups)<=limit,'window/group bound')
        self.highwater=max(self.highwater,receiver.buffered)
    def finish(self,stale):
        self.clock=NOW+100_000_000;self.receiver.expire(self.clock)
        need(not self.receiver.frames and not self.receiver.tickets and not self.receiver.inner.pending
            and self.receiver.inner.orphan_bytes==0,'expiry retained pending state')
        before=list(self.results)
        for record in stale:self.feed(record)
        need(self.results==before,'expired source published')
        self.receiver.configure(SID,2,3,self.clock)
        for record in stale:self.feed(record)
        need(self.results==before and self.receiver.published is None,'old configuration published')

def arrival_orders():
    counts=Counter()
    for k in (4,8,16):
        for omitted in (0,2):
            template=Replay();records=template.issue(1,k,(1,1,16))
            originals=[p for p in records if is_source(p)];parity=next(p for p in records if not is_source(p))
            need([t.vxb.HEADER.unpack_from(p,t.HEADER.size)[2] for p in originals]==[1,2,1,2],'independent window fixture population')
            for mode in ('valid','overlong_zero','invalid_recovered_header'):
                body=parity[t.HEADER.size:]
                if mode=='overlong_zero':body+=b'\0'
                elif mode=='invalid_recovered_header':body=bytes([body[0]^1])+body[1:]
                changed=outer_reseal(parity[:t.HEADER.size]+body)
                schedule=[p for i,p in enumerate(originals) if i!=omitted]+[changed]
                for order in itertools.permutations(range(4)):
                    replay=Replay();replay.issue(1,k,(1,1,16))
                    for i in order:replay.feed(schedule[i])
                    # With one duplicate manifest omitted, all three originals
                    # are necessary. Malformed parity is harmless only if it
                    # arrives after the already-valid stereo publication.
                    expected=mode=='valid' or order[-1]==3
                    need(bool(replay.results)==expected,'publication-order contract failed')
                    replay.finish(records);counts['cases']+=1
                    counts['published' if replay.results else 'withheld']+=1
    need(counts==Counter(cases=432,published=216,withheld=216),'arrival campaign coverage changed')
    return dict(counts)

def seeded(trials):
    coverage=Counter();joint=Counter();stats=Counter();published=withheld=controls=highwater=0
    for dims in DIMENSIONS:
        for k in (4,8,16):
            for loss in (False,True):
                replay=Replay();all_records=[]
                for frame in (1,2):
                    records=replay.issue(frame,k,dims);all_records+=records
                    omitted=next(i for i,p in enumerate(records) if is_codec(p)) if loss else -1
                    for i,p in enumerate(records):
                        if i!=omitted:replay.feed(p)
                need(replay.results==[1,2],'clean/single-source-repair control failed')
                replay.finish(all_records);controls+=1
    for trial in range(trials):
        rng=random.Random(0x565747310000+trial);kind=MUTATORS[trial%len(MUTATORS)]
        cycle=trial//len(MUTATORS);k=(4,8,16)[cycle%3];dims=DIMENSIONS[(cycle//3)%len(DIMENSIONS)]
        replay=Replay();records=replay.issue(1,k,dims)+replay.issue(2,k,dims);stale=list(records)
        candidates=[i for i,p in enumerate(records) if (not is_source(p) if kind.startswith('parity_') else is_codec(p) if kind.startswith('inner_') else True)]
        need(candidates,'mutator lacks target');index=rng.choice(candidates);records[index]=mutate(records[index],kind,rng)
        records=[p for p in records if rng.random()>=.08]
        records=[q for p in records for q in ([p,p] if rng.random()<.1 else [p])]
        rng.shuffle(records)
        for p in records:replay.feed(p)
        published+=len(replay.results);withheld+=not replay.results;highwater=max(highwater,replay.highwater)
        replay.finish(stale);coverage[kind]+=1;joint[kind+'/'+str(k)+'/'+'x'.join(map(str,dims))]+=1
        stats.update(replay.receiver.stats)
    need(set(coverage)==set(MUTATORS) and all(v>=100 for v in coverage.values()),'insufficient mutation coverage')
    need(len(joint)==len(MUTATORS)*3*len(DIMENSIONS) and set(joint.values())=={trials//len(joint)},'incomplete joint mutation/group/shape matrix')
    return dict(trials=trials,controls=controls,published_pairs=published,trials_withheld=withheld,
        coverage=dict(coverage),joint_coverage=dict(joint),receiver_stats=dict(stats),observed_post_receive_retained_payload_highwater=highwater,
        duplicate_loss_reorder=True,old_configuration_and_expiry_replayed=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--trials',type=int,default=4800);args=parser.parse_args()
    need(2400<=args.trials<=19200 and args.trials%(len(MUTATORS)*3*len(DIMENSIONS))==0,'bounded complete mutation population required')
    paths=[Path(__file__),Path(t.__file__),Path(t.vxb.__file__),Path(t.base.__file__)]
    before={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths}
    for path in paths[1:]:
        raw=subprocess.check_output(['git','-C',str(ROOT),'cat-file','blob',PIN+':'+str(path.relative_to(ROOT))])
        need(sha(raw)==before[str(path.relative_to(ROOT))],'protocol differs from pinned qualified branch')
    result=dict(schema=1,status='pass',protocol_commit=PIN,arrival_orders=arrival_orders(),seeded=seeded(args.trials))
    need(before=={str(p.relative_to(ROOT)):sha(p.read_bytes()) for p in paths},'campaign/protocol source changed')
    result.update(source_sha256=before,sources_unchanged=True,sockets_opened=False,headset_operations=False,
        measurement=False,promotion_eligible=False,limitations=[
            'Finite synthetic raw-RRF byte containment and lifecycle campaign, not an exhaustive grammar proof or native pixel test.',
            'Outer CRC is repaired for semantic mutations; inner header CRC is also repaired for inner mutations, but block checksums are not recomputed to authenticate changed content.',
            'CRC/XOR and issued handles are not authentication. Deliberately self-consistent malicious replacement pixels are outside this oracle.',
            'A malformed redundant packet arriving after complete valid publication cannot retroactively invalidate that publication.',
            'Payload highwater excludes temporary allocations, metadata and published pairs; no RSS or latency claim.'])
    with args.out.open('x') as stream:json.dump(result,stream,indent=2);stream.write('\n')
    print(json.dumps(dict(status=result['status'],arrival_orders=result['arrival_orders'],trials=result['seeded']['trials'],
        controls=result['seeded']['controls'],joint_cells=len(result['seeded']['joint_coverage']),
        published_pairs=result['seeded']['published_pairs'],trials_withheld=result['seeded']['trials_withheld'])))

if __name__=='__main__':main()
