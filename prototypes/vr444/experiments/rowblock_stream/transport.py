#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""VXB1 offline independent-row-block transport. CRC is not authentication."""
from dataclasses import dataclass,field
import importlib.util
from pathlib import Path
import struct
import sys
import time
import zlib

spec=importlib.util.spec_from_file_location('vxb_lifetime',Path(__file__).resolve().parents[1]/'datagram_v2/transport.py')
base=importlib.util.module_from_spec(spec);sys.modules[spec.name]=base;spec.loader.exec_module(base)
HEADER=struct.Struct('<4sBBBBIIQQHHHHIIHHI')
MANIFEST_BODY=struct.Struct('<16sQIIHHI')
MANIFEST,DATA=1,2
SHARD=1120
MAX_PAIR=base.MAX_PAIR
MAX_BLOCKS=256
MAX_BUFFERED=2*(MAX_PAIR+MANIFEST_BODY.size)
assert HEADER.size==56 and MANIFEST_BODY.size==40 and HEADER.size+SHARD<=1200


def need(condition,reason):
    if not condition:raise ValueError(reason)


def crc(data):return zlib.crc32(data)


def u32(data,at):return struct.unpack_from('<I',data,at)[0]


def shape(width,height,rows):
    need(all(type(n) is int for n in (width,height,rows)),'shape types')
    need(0<width<=4096 and 0<height<=4096 and width*height<=4194304 and rows in (16,32,64,128),'shape bounds')
    count=(height+rows-1)//rows;need(count<=MAX_BLOCKS,'block count');return count


def lz4_grammar(body,raw):
    at=produced=last_match=0;matched=False
    def length(initial):
        nonlocal at
        value=initial
        if initial==15:
            while True:
                need(at<len(body),'LZ4 extension');extra=body[at];at+=1;value+=extra
                need(value<=raw,'LZ4 length')
                if extra!=255:break
        return value
    while at<len(body):
        token=body[at];at+=1;literals=length(token>>4)
        need(literals<=len(body)-at and literals<=raw-produced,'LZ4 literals')
        at+=literals;produced+=literals
        if at==len(body):
            need(produced==raw and (raw<5 or literals>=5),'LZ4 final literals')
            need(not matched or raw>=12 and last_match<=raw-12,'LZ4 last match');return
        need(len(body)-at>=2,'LZ4 offset bytes');offset=body[at]|body[at+1]<<8;at+=2
        need(0<offset<=produced,'LZ4 offset');match=length(token&15)+4
        need(match<=raw-produced,'LZ4 match');last_match=produced;matched=True;produced+=match
    need(False,'LZ4 ending')


def inspect_rrf(wire,width,height):
    need(32<len(wire)<=width*height*3+32,'RRF size')
    need(wire[:4]==b'RRF1' and wire[4]==1 and wire[5]<=2 and wire[6:8]==b'\0\0','RRF header')
    raw=width*height*3;stored=len(wire)-32
    need((u32(wire,8),u32(wire,12),u32(wire,16),u32(wire,20))==(width,height,stored,raw),'RRF shape/length')
    need(stored<raw if wire[5] else stored==raw,'RRF mode length')
    need(u32(wire,28)==crc(wire[:28]) and u32(wire,24)==crc(wire[32:]),'RRF CRC')
    if wire[5]:lz4_grammar(wire[32:],raw)


def inspect_rrb(wire):
    need(32<len(wire)<=MAX_PAIR,'RRB size')
    need(wire[:5]==b'RRB1\1' and wire[5] in (4,5,6,7) and wire[6:8]==b'\0\0','RRB header')
    width,height=u32(wire,8),u32(wire,12);rows=1<<wire[5];count=shape(width,height,rows)
    need(u32(wire,16)==count and u32(wire,20)==len(wire) and len(wire)>=32+8*count,'RRB count/size')
    need(u32(wire,28)==crc(wire[:28]) and u32(wire,24)==crc(wire[32:32+8*count]),'RRB CRC')
    cursor=32+8*count;blocks=[]
    for index in range(count):
        offset,length=struct.unpack_from('<II',wire,32+8*index)
        need(offset==cursor and length<=len(wire)-cursor,'RRB table')
        block=wire[cursor:cursor+length];inspect_rrf(block,width,min(rows,height-index*rows));blocks.append(block);cursor+=length
    need(cursor==len(wire),'RRB trailing');return width,height,rows,tuple(blocks)


def assemble(width,height,rows,blocks):
    count=shape(width,height,rows);need(len(blocks)==count,'assembly block count')
    cursor=32+8*count;table=bytearray()
    for index,block in enumerate(blocks):
        inspect_rrf(block,width,min(rows,height-index*rows));need(len(block)<=MAX_PAIR-cursor,'assembly cap')
        table+=struct.pack('<II',cursor,len(block));cursor+=len(block)
    prefix=struct.pack('<4sBBBBIIIII',b'RRB1',1,rows.bit_length()-1,0,0,width,height,count,cursor,crc(table))
    need(len(prefix)==28,'assembly prefix');return prefix+struct.pack('<I',crc(prefix))+table+b''.join(blocks)


def packet(kind,eye,se,ce,frame,warp,block,count,index,shards,length,checksum,body):
    prefix=HEADER.pack(b'VXB1',1,kind,eye,0,se,ce,frame,warp,block,count,index,shards,length,checksum,len(body),0,0)
    return prefix[:52]+struct.pack('<I',crc(prefix[:52]+body))+body


class Sender:
    """Emit independently completed RRF blocks; abort permanently on budget/error.

    Total lengths need not be known before the first block. A later over-budget
    block aborts this frame, whose incomplete receiver candidate expires. This
    behavior is explicitly different from pre-encoding a whole admitted pair.
    """
    def __init__(self,session,se,ce,frame,warp,deadline,width,height,rows):
        need(len(session)==16,'session length')
        need(all(type(n) is int for n in (se,ce,frame,warp,deadline)),'identity types')
        need(0<se<2**32 and 0<ce<2**32 and 0<frame<2**64 and 0<warp<2**64 and 0<deadline<2**64,'identity bounds')
        self.count=shape(width,height,rows);self.width,self.height,self.rows=width,height,rows
        self.se,self.ce,self.frame,self.warp=se,ce,frame,warp
        self.reserved=64+16*self.count;self.seen=set();self.failed=False;self.started=False
        body=MANIFEST_BODY.pack(session,deadline,width,height,rows,self.count,0)
        self.manifest=packet(MANIFEST,255,se,ce,frame,warp,0,0,0,0,0,0,body)

    def add_block(self,eye,index,wire):
        need(not self.failed,'sender aborted')
        try:
            need(type(eye) is int and eye in (0,1) and type(index) is int and 0<=index<self.count,'block identity')
            need((eye,index) not in self.seen,'duplicate source block')
            need(len(wire)<=MAX_PAIR-self.reserved,'stereo cap')
            inspect_rrf(wire,self.width,min(self.rows,self.height-index*self.rows))
            shards=(len(wire)+SHARD-1)//SHARD;checksum=crc(wire)
            records=[packet(DATA,eye,self.se,self.ce,self.frame,self.warp,index,self.count,i,shards,len(wire),checksum,wire[i*SHARD:(i+1)*SHARD]) for i in range(shards)]
            if not self.started:records=[self.manifest,records[0],self.manifest,*records[1:]]
            self.started=True;self.seen.add((eye,index));self.reserved+=len(wire);return records
        except Exception:
            self.failed=True;raise

    @property
    def complete(self):return not self.failed and len(self.seen)==2*self.count


def packetize(session,se,ce,frame,warp,deadline,left,right,order=None):
    need(len(left)+len(right)<=MAX_PAIR,'stereo cap')
    l,r=inspect_rrb(left),inspect_rrb(right);need(l[:3]==r[:3],'equal stereo profile')
    sender=Sender(session,se,ce,frame,warp,deadline,*l[:3]);blocks=(l[3],r[3])
    expected={(eye,i) for eye in (0,1) for i in range(sender.count)}
    if order is None:order=[(eye,i) for i in range(sender.count) for eye in (0,1)]
    need(len(order)==len(expected) and set(order)==expected,'complete block order')
    records=[]
    for eye,index in order:records.extend(sender.add_block(eye,index,blocks[eye][index]))
    need(sender.complete,'source completion');return records


@dataclass
class Candidate:
    frame:int
    warp:int
    arrived:int
    manifest:bytes|None=None
    deadline:int|None=None
    width:int=0
    height:int=0
    rows:int=0
    count:int=0
    desc:dict=field(default_factory=dict)
    fragments:dict=field(default_factory=dict)
    blocks:dict=field(default_factory=dict)
    @property
    def bytes(self):return sum(map(len,self.fragments.values()))+sum(map(len,self.blocks.values()))+(len(self.manifest) if self.manifest else 0)
    @property
    def reserved(self):return 64+16*self.count+sum(d[0] for d in self.desc.values())


class Receiver(base.Receiver):
    """Incremental private block assembly; publishes only a complete RRB stereo pair."""
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self.block_highwater=0

    def _parse_manifest(self,body,now,frame,warp):
        need(len(body)==MANIFEST_BODY.size,'manifest size')
        session,deadline,width,height,rows,count,reserved=MANIFEST_BODY.unpack(body)
        need(session==self.session,'session UUID');need(reserved==0 and count==shape(width,height,rows),'manifest shape')
        need(now<deadline<=now+base.MAX_FUTURE_NS and self.tickets.get((frame,warp))==deadline,'unissued/expired deadline')
        return deadline,width,height,rows,count

    @staticmethod
    def _description(c,key,desc):
        eye,block=key;length,checksum,shards=desc
        need(eye in (0,1) and block<c.count,'block identity')
        if c.manifest is not None:
            need(length<=c.width*min(c.rows,c.height-block*c.rows)*3+32,'block raw bound')

    def _complete(self,c,now):
        for key,desc in c.desc.items():
            if key in c.blocks:continue
            length,checksum,shards=desc
            if any((*key,i) not in c.fragments for i in range(shards)):continue
            wire=b''.join(c.fragments[*key,i] for i in range(shards))
            need(len(wire)==length and crc(wire)==checksum,'block CRC')
            inspect_rrf(wire,c.width,min(c.rows,c.height-key[1]*c.rows))
            fresh=max(now,self.clock());need(fresh<c.deadline,'block validation expired')
            c.blocks[key]=wire
            for i in range(shards):del c.fragments[*key,i]
            self.event('completed_private_block',fresh,c.frame)
        self.block_highwater=max(self.block_highwater,len(c.blocks))
        if len(c.blocks)!=2*c.count:return None
        eyes=[assemble(c.width,c.height,c.rows,[c.blocks[eye,i] for i in range(c.count)]) for eye in (0,1)]
        need(len(eyes[0])+len(eyes[1])<=MAX_PAIR,'complete stereo cap')
        fresh=max(now,self.clock());need(fresh<c.deadline,'assembly expired')
        pair=base.Pair(c.frame,c.warp,self.session_epoch,self.config_epoch,*eyes,fresh,c.deadline)
        self.published=pair
        self.tickets={key:deadline for key,deadline in self.tickets.items() if key[0]>c.frame}
        for frame in list(self.pending):
            if frame<=c.frame:self.pending.pop(frame)
        self.event('published_pair',fresh,c.frame);return pair

    def receive(self,record,now):
        self.expire(now);self.stats['received_datagrams']+=1;self.stats['received_udp_payload_bytes']+=len(record)
        if not HEADER.size<len(record)<=1200:self.event('packet size',now);return None
        fields=HEADER.unpack_from(record)
        magic,version,kind,eye,res,se,ce,frame,warp,block,count,index,shards,length,checksum,size,res2,header_crc=fields
        body=record[HEADER.size:]
        if magic!=b'VXB1' or version!=1 or kind not in (MANIFEST,DATA) or res or res2 or not frame or not warp or size!=len(body) or crc(record[:52]+body)!=header_crc:
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
                need(shards==(length+SHARD-1)//SHARD and 0<=index<shards and len(body)==min(SHARD,length-index*SHARD),'fragment size')
        except ValueError as error:self.event(str(error),now,frame);return None
        c=self.pending.get(frame)
        if c and c.warp!=warp:self.event('warp conflict',now,frame);return None
        if c is None:
            if len(self.pending)>=base.MAX_CANDIDATES:
                oldest=min(self.pending)
                if frame<=oldest:self.event('candidate cap',now,frame);return None
                self.retire(oldest,'superseded incomplete',now)
            c=Candidate(frame,warp,now);self.pending[frame]=c
        try:
            if kind==MANIFEST:
                if c.manifest is not None:
                    need(c.manifest==body,'manifest conflict');self.event('duplicate manifest',now,frame);return None
                deadline,width,height,rows,n=parsed
                need(c.count in (0,n),'orphan block count')
                c.manifest=body;c.deadline,c.width,c.height,c.rows,c.count=deadline,width,height,rows,n
                for key,desc in c.desc.items():self._description(c,key,desc)
            else:
                need(c.count in (0,count),'block count conflict');c.count=count
                key=(eye,block);desc=(length,checksum,shards);self._description(c,key,desc)
                if key in c.desc:need(c.desc[key]==desc,'block descriptor conflict')
                else:c.desc[key]=desc
                need(c.reserved<=MAX_PAIR,'declared stereo cap')
                if key in c.blocks:
                    need(c.blocks[key][index*SHARD:(index+1)*SHARD]==body,'completed block conflict')
                    self.event('duplicate fragment',now,frame);return None
                fkey=(*key,index)
                if fkey in c.fragments:
                    need(c.fragments[fkey]==body,'fragment conflict');self.event('duplicate fragment',now,frame);return None
                need(c.manifest is not None or self.orphan_bytes+len(body)<=base.MAX_ORPHAN,'orphan cap')
                c.fragments[fkey]=body
            need(c.reserved<=MAX_PAIR and self.buffered<=MAX_BUFFERED,'buffer cap')
            self.highwater_bytes=max(self.highwater_bytes,self.buffered)
            self.highwater_candidates=max(self.highwater_candidates,len(self.pending))
            return self._complete(c,now) if c.manifest is not None else None
        except ValueError as error:self.retire(frame,str(error),max(now,self.clock()));return None
