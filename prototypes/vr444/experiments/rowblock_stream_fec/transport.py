#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""VXP1 independent-block XOR repair, an offline extension distinct from VXB1."""
from dataclasses import dataclass,field
import importlib.util
from pathlib import Path
import struct
import sys

spec=importlib.util.spec_from_file_location('vxp_rowblocks',Path(__file__).resolve().parents[1]/'rowblock_stream/transport.py')
vxb=importlib.util.module_from_spec(spec);sys.modules[spec.name]=vxb;spec.loader.exec_module(vxb)
base=vxb.base
HEADER,MANIFEST_BODY=vxb.HEADER,vxb.MANIFEST_BODY
MANIFEST,DATA,PARITY=1,2,3
SHARD,MAX_PAIR,MAX_BLOCKS=vxb.SHARD,vxb.MAX_PAIR,vxb.MAX_BLOCKS
# Each group's longest source is no larger than its summed source lengths.
# Therefore retained parity per candidate is at most its complete source bytes.
MAX_BUFFERED=2*(2*MAX_PAIR+MANIFEST_BODY.size)
need,crc,shape,inspect_rrb,inspect_rrf,assemble=vxb.need,vxb.crc,vxb.shape,vxb.inspect_rrb,vxb.inspect_rrf,vxb.assemble

def packet(kind,eye,se,ce,frame,warp,block,count,index,shards,length,checksum,body,k=8):
    need(type(k) is int and k in (4,8,16),'FEC group size')
    prefix=HEADER.pack(b'VXP1',1,kind,eye,k,se,ce,frame,warp,block,count,index,shards,length,checksum,len(body),0,0)
    return prefix[:52]+struct.pack('<I',crc(prefix[:52]+body))+body

def members(length,k,group):
    count=(length+SHARD-1)//SHARD
    return range(group*k,min(count,(group+1)*k))

def parity_size(length,k,group):return min(SHARD,length-group*k*SHARD)

def xor_group(wire,k,group):
    output=bytearray(parity_size(len(wire),k,group))
    for index in members(len(wire),k,group):
        for at,value in enumerate(wire[index*SHARD:(index+1)*SHARD]):output[at]^=value
    return bytes(output)

class Sender(vxb.Sender):
    def __init__(self,*args,k=8,**kwargs):
        need(type(k) is int and k in (4,8,16),'FEC group size');super().__init__(*args,**kwargs);self.k=k
        self.manifest=packet(MANIFEST,255,self.se,self.ce,self.frame,self.warp,0,0,0,0,0,0,self.manifest[HEADER.size:],k)

    def add_block(self,eye,index,wire):
        try:
            plain=super().add_block(eye,index,wire);records=[]
            for record in plain:
                fields=HEADER.unpack_from(record)
                if fields[2]==MANIFEST:records.append(record);continue
                body=record[HEADER.size:];shard,shards=fields[11:13]
                records.append(packet(DATA,eye,self.se,self.ce,self.frame,self.warp,index,self.count,shard,shards,len(wire),fields[14],body,self.k))
                if (shard+1)%self.k==0 or shard+1==shards:
                    group=shard//self.k
                    records.append(packet(PARITY,eye,self.se,self.ce,self.frame,self.warp,index,self.count,group,shards,len(wire),fields[14],xor_group(wire,self.k,group),self.k))
            return records
        except Exception:self.failed=True;raise

def packetize(session,se,ce,frame,warp,deadline,left,right,order=None,k=8):
    need(len(left)+len(right)<=MAX_PAIR,'stereo cap')
    l,r=inspect_rrb(left),inspect_rrb(right);need(l[:3]==r[:3],'equal stereo profile')
    sender=Sender(session,se,ce,frame,warp,deadline,*l[:3],k=k);blocks=(l[3],r[3])
    expected={(eye,i) for eye in (0,1) for i in range(sender.count)}
    if order is None:order=[(eye,i) for i in range(sender.count) for eye in (0,1)]
    need(len(order)==len(expected) and set(order)==expected,'complete block order')
    records=[]
    for eye,index in order:records.extend(sender.add_block(eye,index,blocks[eye][index]))
    need(sender.complete,'source completion');return records

@dataclass
class Candidate(vxb.Candidate):
    k:int=0
    parity:dict=field(default_factory=dict)
    @property
    def bytes(self):return super().bytes+sum(map(len,self.parity.values()))

class Receiver(vxb.Receiver):
    def _complete(self,c,now):
        for (eye,block,group),repair in list(c.parity.items()):
            key=(eye,block);length,checksum,shards=c.desc[key]
            indices=members(length,c.k,group)
            missing=[i for i in indices if (*key,i) not in c.fragments]
            if len(missing)>1:continue
            recovered=bytearray(repair)
            for i in indices:
                if i in missing:continue
                for at,value in enumerate(c.fragments[*key,i]):recovered[at]^=value
            if not missing:
                need(not any(recovered),'parity conflict')
            else:
                size=min(SHARD,length-missing[0]*SHARD)
                need(not any(recovered[size:]),'recovered tail padding')
                c.fragments[*key,missing[0]]=bytes(recovered[:size])
                self.event('recovered_private_fragment',max(now,self.clock()),c.frame)
            del c.parity[eye,block,group]
        need(self.buffered<=MAX_BUFFERED,'repair payload cap')
        self.highwater_bytes=max(self.highwater_bytes,self.buffered)
        # Base validation checks every complete block CRC/grammar and fresh
        # deadline, then both canonical eyes and another fresh clock before
        # returning any published pair. A repaired fragment alone publishes none.
        result=super()._complete(c,now)
        for key in list(c.parity):
            if key[:2] in c.blocks:del c.parity[key]
        return result

    def receive(self,record,now):
        self.expire(now);self.stats['received_datagrams']+=1;self.stats['received_udp_payload_bytes']+=len(record)
        if not HEADER.size<len(record)<=1200:self.event('packet size',now);return None
        fields=HEADER.unpack_from(record)
        magic,version,kind,eye,k,se,ce,frame,warp,block,count,index,shards,length,checksum,size,res,header_crc=fields
        body=record[HEADER.size:]
        if (magic!=b'VXP1' or version!=1 or kind not in (MANIFEST,DATA,PARITY) or k not in (4,8,16) or res
                or not frame or not warp or size!=len(body) or crc(record[:52]+body)!=header_crc):
            self.event('packet header/CRC',now,frame);return None
        if se!=self.session_epoch or ce!=self.config_epoch:self.event('epoch',now,frame);return None
        if frame in self.retired or self.published and frame<=self.published.frame:self.event('stale frame',now,frame);return None
        try:
            parsed=None
            if kind==MANIFEST:
                need((eye,block,count,index,shards,length,checksum)==(255,0,0,0,0,0,0),'manifest fields')
                parsed=self._parse_manifest(body,now,frame,warp)
            else:
                need(eye in (0,1) and 0<=block<count<=MAX_BLOCKS and 35<=length<=MAX_PAIR,'fragment identity')
                need(shards==(length+SHARD-1)//SHARD,'source shard count')
                if kind==DATA:need(0<=index<shards and len(body)==min(SHARD,length-index*SHARD),'fragment size')
                else:need(0<=index<(shards+k-1)//k and len(body)==parity_size(length,k,index),'parity size')
        except ValueError as error:self.event(str(error),now,frame);return None
        c=self.pending.get(frame)
        if c and c.warp!=warp:self.event('warp conflict',now,frame);return None
        if c is None:
            if len(self.pending)>=base.MAX_CANDIDATES:
                oldest=min(self.pending)
                if frame<=oldest:self.event('candidate cap',now,frame);return None
                self.retire(oldest,'superseded incomplete',now)
            c=Candidate(frame,warp,now,k=k);self.pending[frame]=c
        try:
            need(c.k==k,'FEC group conflict')
            if kind==MANIFEST:
                if c.manifest is not None:
                    need(c.manifest==body,'manifest conflict');self.event('duplicate manifest',now,frame);return None
                deadline,width,height,rows,n=parsed;need(c.count in (0,n),'orphan block count')
                c.manifest=body;c.deadline,c.width,c.height,c.rows,c.count=deadline,width,height,rows,n
                for key,desc in c.desc.items():self._description(c,key,desc)
            else:
                need(c.count in (0,count),'block count conflict');c.count=count
                key=(eye,block);desc=(length,checksum,shards);self._description(c,key,desc)
                if key in c.desc:need(c.desc[key]==desc,'block descriptor conflict')
                else:c.desc[key]=desc
                need(c.reserved<=MAX_PAIR,'declared stereo cap')
                if key in c.blocks:
                    expected=(c.blocks[key][index*SHARD:(index+1)*SHARD] if kind==DATA else xor_group(c.blocks[key],k,index))
                    need(expected==body,'completed block conflict');self.event('duplicate completed record',now,frame);return None
                storage=c.fragments if kind==DATA else c.parity
                fkey=(*key,index)
                if fkey in storage:
                    need(storage[fkey]==body,'fragment/parity conflict');self.event('duplicate record',now,frame);return None
                need(c.manifest is not None or self.orphan_bytes+len(body)<=base.MAX_ORPHAN,'orphan cap')
                storage[fkey]=body
            need(c.reserved<=MAX_PAIR and self.buffered<=MAX_BUFFERED,'buffer cap')
            self.highwater_bytes=max(self.highwater_bytes,self.buffered)
            self.highwater_candidates=max(self.highwater_candidates,len(self.pending))
            return self._complete(c,now) if c.manifest is not None else None
        except ValueError as error:self.retire(frame,str(error),max(now,self.clock()));return None
