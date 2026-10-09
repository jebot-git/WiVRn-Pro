#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""VWG1 bounded packet-window XOR wrapper over complete VXB1 records.

Receiver-issued compact handles bind the full session/config/frame/warp/deadline.
They are not authentication. All source/recovered inner records are validated.
"""
from collections import Counter
from dataclasses import dataclass,field
import importlib.util
from pathlib import Path
import struct
import sys
import time

spec=importlib.util.spec_from_file_location('vwg_vxb',Path(__file__).resolve().parents[1]/'rowblock_stream/transport.py')
vxb=importlib.util.module_from_spec(spec);sys.modules[spec.name]=vxb;spec.loader.exec_module(vxb)
base=vxb.base
need,crc=vxb.need,vxb.crc
HEADER=struct.Struct('<4sBBBBHHQI')
DATA,PARITY=1,2
MAX_RECORD=1176
MAX_RECORDS=(vxb.MAX_PAIR+vxb.SHARD-1)//vxb.SHARD+2*vxb.MAX_BLOCKS+2
MAX_SOURCE_BYTES=vxb.MAX_PAIR+vxb.HEADER.size*MAX_RECORDS+2*(vxb.HEADER.size+vxb.MANIFEST_BODY.size)
MAX_WINDOW_BYTES=2*MAX_SOURCE_BYTES
MAX_BUFFERED=2*(MAX_WINDOW_BYTES+vxb.MAX_PAIR+vxb.MANIFEST_BODY.size)
assert HEADER.size==24 and HEADER.size+MAX_RECORD==1200

def packet(kind,k,slot,group,count,handle,body):
    prefix=HEADER.pack(b'VWG1',1,kind,k,slot,group,count,handle,0)
    return prefix[:20]+struct.pack('<I',crc(prefix[:20]+body))+body

def xor_records(records):
    need(records and all(vxb.HEADER.size<len(r)<=MAX_RECORD for r in records),'protected record size')
    result=bytearray(max(map(len,records)))
    for record in records:
        for i,value in enumerate(record):result[i]^=value
    return bytes(result)

class Sender:
    """Forwards each source immediately; emits parity after K records or finish.

    finish() is required to seal a partial final group. A later codec/budget
    error permanently aborts this frame; already emitted fragments stay private.
    """
    def __init__(self,handle,session,se,ce,frame,warp,deadline,width,height,rows,k=8):
        need(type(handle) is int and 0<handle<2**64 and type(k) is int and k in (4,8,16),'handle/group bounds')
        self.handle,self.k=handle,k;self.inner=vxb.Sender(session,se,ce,frame,warp,deadline,width,height,rows)
        self.group=0;self.records=[];self.emitted=0;self.closed=False;self.failed=False
    def _source(self,record):
        need(self.emitted<MAX_RECORDS and len(record)<=MAX_RECORD,'source record bound')
        output=[packet(DATA,self.k,len(self.records),self.group,0,self.handle,record)]
        self.records.append(record);self.emitted+=1
        if len(self.records)==self.k:output+=self._flush()
        return output
    def _flush(self):
        if not self.records:return []
        result=[packet(PARITY,self.k,0,self.group,len(self.records),self.handle,xor_records(self.records))]
        self.records=[];self.group+=1;return result
    def add_block(self,eye,index,wire):
        need(not self.closed and not self.failed,'sender closed/aborted')
        try:
            output=[]
            for record in self.inner.add_block(eye,index,wire):output+=self._source(record)
            return output
        except Exception:self.failed=True;raise
    def finish(self):
        need(not self.closed and not self.failed,'sender closed/aborted')
        try:
            need(self.inner.complete,'incomplete source frame');output=self._flush();self.closed=True;return output
        except Exception:self.failed=True;raise

def packetize(handle,session,se,ce,frame,warp,deadline,left,right,k=8,order=None):
    need(len(left)+len(right)<=vxb.MAX_PAIR,'stereo cap')
    l,r=vxb.inspect_rrb(left),vxb.inspect_rrb(right);need(l[:3]==r[:3],'equal stereo profile')
    sender=Sender(handle,session,se,ce,frame,warp,deadline,*l[:3],k=k);blocks=(l[3],r[3]);count=len(l[3])
    expected={(eye,i) for eye in (0,1) for i in range(count)}
    if order is None:order=[(eye,i) for i in range(count) for eye in (0,1)]
    need(len(order)==len(expected) and set(order)==expected,'complete block order')
    output=[]
    for eye,index in order:output+=sender.add_block(eye,index,blocks[eye][index])
    return output+sender.finish()

@dataclass(frozen=True)
class Ticket:
    frame:int
    warp:int
    deadline:int

@dataclass
class Group:
    sources:dict=field(default_factory=dict)
    parity:bytes|None=None
    count:int|None=None
    @property
    def bytes(self):return sum(map(len,self.sources.values()))+(len(self.parity) if self.parity else 0)

@dataclass
class Frame:
    k:int
    groups:dict=field(default_factory=dict)
    done:set=field(default_factory=set)
    @property
    def bytes(self):return sum(g.bytes for g in self.groups.values())

class Receiver:
    def __init__(self,session,se=1,ce=1,clock=time.monotonic_ns):
        self.inner=vxb.Receiver(session,se,ce,clock=clock);self.clock=clock
        self.tickets={};self.frames={};self.next_handle=1;self.stats=Counter();self.highwater=0
    @property
    def published(self):return self.inner.published
    @property
    def buffered(self):return sum(f.bytes for f in self.frames.values())+self.inner.buffered
    def configure(self,session,se,ce,now):
        self.inner.configure(session,se,ce,now);self.tickets.clear();self.frames.clear()
        # Handles never restart on configuration changes within this receiver.
    def issue(self,now,ttl,frame,warp):
        need(all(type(v) is int for v in (now,ttl,frame,warp)) and 0<=now<2**64 and 0<ttl<=base.MAX_FUTURE_NS and now+ttl<2**64,'ticket types/time')
        self.expire(now)
        for handle,ticket in self.tickets.items():
            if ticket.frame==frame:
                need(ticket.warp==warp and ticket.deadline==now+ttl,'frame handle cannot change');return handle,ticket.deadline
        need(len(self.tickets)<base.MAX_TICKETS and self.next_handle<2**64,'handle cap')
        need(not self.published or frame>self.published.frame,'published frame cannot get a new handle')
        need(frame not in self.inner.retired,'retired frame cannot get a new handle')
        deadline=self.inner.issue_deadline(now,ttl,frame,warp);handle=self.next_handle;self.next_handle+=1
        self.tickets[handle]=Ticket(frame,warp,deadline);return handle,deadline
    def _retire(self,handle,reason,now):
        ticket=self.tickets.pop(handle,None);self.frames.pop(handle,None)
        if ticket:self.inner.retire(ticket.frame,'wrapper '+reason,now)
        self.stats[reason]+=1
    def expire(self,now):
        self.inner.expire(now)
        for handle,ticket in list(self.tickets.items()):
            if now>=ticket.deadline or ticket.frame in self.inner.retired or self.published and ticket.frame<=self.published.frame:
                self.tickets.pop(handle);self.frames.pop(handle,None)
    def _published(self,pair):
        for handle,ticket in list(self.tickets.items()):
            if ticket.frame<=pair.frame:self.tickets.pop(handle);self.frames.pop(handle,None)
        self.stats['published_pair']+=1;return pair
    def _validate_inner(self,record,ticket,now):
        need(vxb.HEADER.size<len(record)<=MAX_RECORD,'inner record size')
        f=vxb.HEADER.unpack_from(record);body=record[vxb.HEADER.size:]
        need(f[0]==b'VXB1' and f[1]==1 and f[2] in (vxb.MANIFEST,vxb.DATA) and f[4]==0 and f[16]==0
            and f[15]==len(body) and f[17]==crc(record[:52]+body),'inner header/CRC')
        need(f[5:9]==(self.inner.session_epoch,self.inner.config_epoch,ticket.frame,ticket.warp),'inner ticket identity')
        if f[2]==vxb.MANIFEST:
            need((f[3],*f[9:15])==(255,0,0,0,0,0,0),'inner manifest fields')
            self.inner._parse_manifest(body,now,ticket.frame,ticket.warp)
        else:
            eye,block,count,index,shards,length=f[3],f[9],f[10],f[11],f[12],f[13]
            need(eye in (0,1) and 0<=block<count<=vxb.MAX_BLOCKS and 35<=length<=vxb.MAX_PAIR,'inner source identity')
            need(shards==(length+vxb.SHARD-1)//vxb.SHARD and 0<=index<shards
                and len(body)==min(vxb.SHARD,length-index*vxb.SHARD),'inner fragment size')
    def _forward(self,record,ticket,now):
        self._validate_inner(record,ticket,now);pair=self.inner.receive(record,now)
        need(ticket.frame not in self.inner.retired,'inner candidate rejected')
        return pair
    def _repair(self,state,group_id,ticket,now):
        group=state.groups[group_id]
        if group.parity is None:
            if len(group.sources)==state.k:state.done.add(group_id);del state.groups[group_id]
            return None
        missing=[i for i in range(group.count) if i not in group.sources]
        if len(missing)>1:return None
        recovered=bytearray(group.parity)
        for record in group.sources.values():
            need(len(record)<=len(recovered),'parity shorter than source')
            for i,value in enumerate(record):recovered[i]^=value
        if not missing:
            need(not any(recovered),'parity mismatch')
        else:
            need(len(recovered)>vxb.HEADER.size,'recovered header size')
            f=vxb.HEADER.unpack_from(recovered);length=vxb.HEADER.size+f[15]
            need(vxb.HEADER.size<length<=len(recovered) and not any(recovered[length:]),'recovered size/padding')
            record=bytes(recovered[:length]);self._validate_inner(record,ticket,now)
            group.sources[missing[0]]=record;need(state.bytes<=MAX_WINDOW_BYTES,'repaired window cap')
            need(len(group.parity)==max(map(len,group.sources.values())),'noncanonical parity length')
            self.stats['recovered_record']+=1
            pair=self._forward(record,ticket,now)
            if pair:return pair
        need(len(group.parity)==max(map(len,group.sources.values())),'noncanonical parity length')
        state.done.add(group_id);del state.groups[group_id];return None
    def receive(self,record,now):
        self.expire(now);self.stats['received_records']+=1
        if not HEADER.size+vxb.HEADER.size<len(record)<=1200:self.stats['outer size']+=1;return None
        magic,version,kind,k,slot,group_id,count,handle,checksum=HEADER.unpack_from(record);body=record[HEADER.size:]
        if (magic!=b'VWG1' or version!=1 or kind not in (DATA,PARITY) or k not in (4,8,16)
                or crc(record[:20]+body)!=checksum or not handle):self.stats['outer header/CRC']+=1;return None
        ticket=self.tickets.get(handle)
        if ticket is None:self.stats['unissued/stale handle']+=1;return None
        try:
            need(group_id*k<MAX_RECORDS,'group count')
            if kind==DATA:need(count==0 and slot<k and group_id*k+slot<MAX_RECORDS,'source slot')
            else:need(slot==0 and 0<count<=k and group_id*k+count<=MAX_RECORDS,'repair count')
            if kind==DATA:self._validate_inner(body,ticket,now)
            state=self.frames.get(handle)
            if state is None:
                if len(self.frames)>=base.MAX_CANDIDATES:
                    oldest=min(self.frames,key=lambda h:self.tickets[h].frame)
                    need(ticket.frame>self.tickets[oldest].frame,'candidate cap')
                    self._retire(oldest,'superseded incomplete',now)
                state=Frame(k);self.frames[handle]=state
            need(state.k==k,'group size conflict')
            if group_id in state.done:self.stats['duplicate completed group']+=1;return None
            group=state.groups.setdefault(group_id,Group())
            if kind==DATA:
                need(group.count is None or slot<group.count,'source past sealed group')
                if slot in group.sources:
                    need(group.sources[slot]==body,'source conflict');self.stats['duplicate source']+=1;return None
                need(state.bytes+len(body)<=MAX_WINDOW_BYTES,'window payload cap');group.sources[slot]=body
                pair=self._forward(body,ticket,now)
                if pair:return self._published(pair)
            else:
                need(not group.sources or max(group.sources)<count,'repair count conflicts with source')
                if group.parity is not None:
                    need(group.count==count and group.parity==body,'repair conflict');self.stats['duplicate parity']+=1;return None
                need(state.bytes+len(body)<=MAX_WINDOW_BYTES,'window payload cap');group.parity=body;group.count=count
            need(self.buffered<=MAX_BUFFERED,'combined payload cap');self.highwater=max(self.highwater,self.buffered)
            pair=self._repair(state,group_id,ticket,now)
            need(self.buffered<=MAX_BUFFERED,'combined repair cap');self.highwater=max(self.highwater,self.buffered)
            return self._published(pair) if pair else None
        except ValueError as error:self._retire(handle,str(error),max(now,self.clock()));return None
