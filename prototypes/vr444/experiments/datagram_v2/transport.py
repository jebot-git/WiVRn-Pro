#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Experimental bounded VRP2 plain UDP assembly. NOT authenticated/production QUIC.
Deadlines are receiver-issued monotonic nanoseconds, never sender wall-clock times.
Opaque independent chunks only: no codecs, temporal references, dictionaries or FEC.
"""
from collections import Counter, OrderedDict
from dataclasses import dataclass, field
import math
import struct
import time
import zlib

HEADER = struct.Struct('<4sBBHIIQQHHHHIHBB')
PREFIX = struct.Struct('<16sQHHI')
DESC = struct.Struct('<HBBIII')
UDP_BUDGET = 1200
PAYLOAD = UDP_BUDGET - HEADER.size
MAX_CHUNK = 16 * 1024
MAX_PAIR = 256 * 1024
MAX_CHUNKS = 32
MAX_CANDIDATES = 2
MAX_TICKETS = 32
MAX_ORPHAN = 64 * 1024
MAX_BUFFERED = 2 * MAX_PAIR + 4096
ORPHAN_TTL_NS = 20_000_000
MAX_FUTURE_NS = 100_000_000
MANIFEST, DATA = 1, 2
assert HEADER.size == 48


def crc(data):
    return zlib.crc32(data)


def header(kind, session_epoch, config_epoch, frame, warp, obj, index, count, payload):
    return HEADER.pack(b'VRP2', 2, kind, 0, session_epoch, config_epoch,
                       frame, warp, obj, index, count, len(payload), 0xffffffff, 0, 0, 0) + payload


def packetize(session, session_epoch, config_epoch, frame, warp, deadline_ns, left, right):
    if len(session) != 16 or not left or not right or len(left)+len(right) > MAX_PAIR:
        raise ValueError('input bounds')
    if min(frame, warp, deadline_ns, session_epoch, config_epoch) <= 0:
        raise ValueError('nonzero identity/deadline')
    chunks=[]
    for eye, data in enumerate((left,right)):
        for at in range(0,len(data),MAX_CHUNK):
            chunks.append((len(chunks)+1,eye,at,data[at:at+MAX_CHUNK]))
    if len(chunks)>MAX_CHUNKS: raise ValueError('chunk count')
    body=PREFIX.pack(session,deadline_ns,2,len(chunks),len(left)+len(right))
    body+=b''.join(DESC.pack(cid,eye,0,len(data),crc(data),at) for cid,eye,at,data in chunks)
    body+=struct.pack('<I',crc(body))
    packets=[header(MANIFEST,session_epoch,config_epoch,frame,warp,0,0,1,body)]
    for cid,eye,at,data in chunks:
        n=math.ceil(len(data)/PAYLOAD)
        packets.extend(header(DATA,session_epoch,config_epoch,frame,warp,cid,i,n,data[i*PAYLOAD:(i+1)*PAYLOAD]) for i in range(n))
    assert all(len(p)<=UDP_BUDGET for p in packets)
    return packets


@dataclass
class Candidate:
    frame: int
    warp: int
    arrived: int
    manifest: bytes | None = None
    deadline: int | None = None
    desc: dict = field(default_factory=dict)
    fragments: dict = field(default_factory=dict)
    @property
    def bytes(self):
        return sum(len(v[1]) for v in self.fragments.values()) + (len(self.manifest) if self.manifest else 0)


@dataclass(frozen=True)
class Pair:
    frame: int
    warp: int
    session_epoch: int
    config_epoch: int
    left: bytes
    right: bytes
    published_receiver_ns: int
    deadline_receiver_ns: int


class Receiver:
    def __init__(self, session, session_epoch=1, config_epoch=1, clock=time.monotonic_ns):
        if len(session)!=16: raise ValueError('session length')
        if not (0 < session_epoch < 2**32 and 0 < config_epoch < 2**32): raise ValueError('epoch bounds')
        self.clock=clock; self.tickets={}
        self.session=session; self.session_epoch=session_epoch; self.config_epoch=config_epoch
        self.pending={}; self.published=None; self.retired=OrderedDict()
        self.stats=Counter(); self.trace=[]; self.trace_dropped=0
        self.highwater_bytes=0; self.highwater_candidates=0

    def event(self, reason, now, frame=0):
        self.stats[reason]+=1
        if len(self.trace)<4096:
            self.trace.append(dict(receiver_monotonic_ns=now,event=reason,frame=frame))
        else: self.trace_dropped+=1

    def configure(self, session, session_epoch, config_epoch, now):
        if len(session)!=16: raise ValueError('session length')
        if not (0 < session_epoch < 2**32 and 0 < config_epoch < 2**32): raise ValueError('epoch bounds')
        if session_epoch < self.session_epoch or (session_epoch == self.session_epoch and
                (session != self.session or config_epoch <= self.config_epoch)):
            raise ValueError('epochs must advance; UUID changes require a new session epoch')
        self.pending.clear(); self.published=None; self.retired.clear(); self.tickets.clear()
        self.session=session; self.session_epoch=session_epoch; self.config_epoch=config_epoch
        self.event('configuration_reset',now)

    def issue_deadline(self, now, ttl_ns, frame, warp):
        if not 0 < ttl_ns <= MAX_FUTURE_NS: raise ValueError('deadline ttl')
        if not (0 < frame < 2**64 and 0 < warp < 2**64): raise ValueError('ticket identity')
        self.expire(now)
        key=(frame,warp); deadline=now+ttl_ns
        if key in self.tickets:
            if self.tickets[key] != deadline: raise ValueError('ticket deadline cannot change')
            return deadline
        if len(self.tickets)>=MAX_TICKETS: raise ValueError('ticket cap')
        self.tickets[key]=deadline
        return deadline

    @property
    def buffered(self): return sum(c.bytes for c in self.pending.values())
    @property
    def orphan_bytes(self): return sum(c.bytes for c in self.pending.values() if c.manifest is None)

    def retire(self, frame, why, now):
        self.pending.pop(frame,None); self.retired[frame]=None
        self.tickets={key:deadline for key,deadline in self.tickets.items() if key[0]!=frame}
        while len(self.retired)>128: self.retired.popitem(last=False)
        self.event(why,now,frame)

    def expire(self, now):
        self.tickets={key:deadline for key,deadline in self.tickets.items() if now < deadline}
        for f,c in list(self.pending.items()):
            if now >= (c.deadline if c.deadline is not None else c.arrived+ORPHAN_TTL_NS):
                self.retire(f,'expired' if c.manifest is not None else 'orphan_expired',now)

    def _manifest(self, body, now, frame, warp):
        if len(body)<PREFIX.size+4: raise ValueError('manifest_size')
        sid,deadline,eyes,n,total=PREFIX.unpack_from(body)
        if sid!=self.session: raise ValueError('session_uuid')
        if eyes!=2 or not 2<=n<=MAX_CHUNKS or not 2<=total<=MAX_PAIR:
            raise ValueError('manifest_bounds')
        if len(body)!=PREFIX.size+n*DESC.size+4: raise ValueError('manifest_size')
        if crc(body[:-4])!=struct.unpack_from('<I',body,len(body)-4)[0]: raise ValueError('manifest_crc')
        if not now < deadline <= now+MAX_FUTURE_NS: raise ValueError('deadline')
        if self.tickets.get((frame,warp)) != deadline: raise ValueError('unissued_deadline')
        desc={}; offsets=[0,0]; size=0
        for i in range(n):
            cid,eye,reserved,length,checksum,offset=DESC.unpack_from(body,PREFIX.size+i*DESC.size)
            if cid!=i+1 or eye>1 or reserved or not 0<length<=MAX_CHUNK or offset!=offsets[eye]:
                raise ValueError('chunk_descriptor')
            offsets[eye]+=length; size+=length
            desc[cid]=(eye,offset,length,checksum)
        if size!=total or not all(offsets): raise ValueError('eye_coverage')
        return deadline,desc

    @staticmethod
    def _fragment_valid(c, key, value):
        obj,index=key; count,body=value
        if obj not in c.desc: return False
        length=c.desc[obj][2]; expected=math.ceil(length/PAYLOAD)
        return count==expected and len(body)==min(PAYLOAD,length-index*PAYLOAD) and 0<=index<expected

    def receive(self, packet, now):
        self.expire(now)
        self.stats['received_datagrams']+=1; self.stats['received_udp_payload_bytes']+=len(packet)
        if not HEADER.size < len(packet) <= UDP_BUDGET:
            self.event('packet_size',now); return None
        magic,version,kind,flags,se,ce,frame,warp,obj,index,count,size,fg,fs,fk,fr=HEADER.unpack_from(packet)
        body=packet[HEADER.size:]
        if magic!=b'VRP2' or version!=2 or kind not in (MANIFEST,DATA) or flags or fg!=0xffffffff or fs or fk or fr or size!=len(body) or not frame or not warp:
            self.event('header',now,frame); return None
        if se!=self.session_epoch or ce!=self.config_epoch:
            self.event('epoch',now,frame); return None
        if frame in self.retired or (self.published and frame<=self.published.frame):
            self.event('stale_frame',now,frame); return None
        parsed=None
        if kind==MANIFEST:
            if (obj,index,count)!=(0,0,1): self.event('manifest_header',now,frame); return None
            try: parsed=self._manifest(body,now,frame,warp)
            except ValueError as e: self.event(str(e),now,frame); return None
        elif not (1<=obj<=MAX_CHUNKS and 0<=index<count<=math.ceil(MAX_CHUNK/PAYLOAD)):
            self.event('fragment_header',now,frame); return None
        c=self.pending.get(frame)
        if c and c.warp!=warp: self.event('warp_conflict',now,frame); return None
        if c is None:
            if len(self.pending)>=MAX_CANDIDATES:
                oldest=min(self.pending)
                if frame<=oldest: self.event('candidate_cap',now,frame); return None
                self.retire(oldest,'superseded_incomplete',now)
            c=Candidate(frame,warp,now); self.pending[frame]=c
        if kind==MANIFEST:
            if c.manifest is not None:
                self.event('duplicate_manifest' if c.manifest==body else 'manifest_conflict',now,frame); return None
            c.manifest=body; c.deadline,c.desc=parsed
            if any(not self._fragment_valid(c,k,v) for k,v in c.fragments.items()):
                self.retire(frame,'orphan_manifest_conflict',now); return None
        else:
            key=(obj,index); value=(count,body)
            if key in c.fragments:
                self.event('duplicate' if c.fragments[key]==value else 'fragment_conflict',now,frame); return None
            if c.manifest is not None and not self._fragment_valid(c,key,value):
                self.event('fragment_length',now,frame); return None
            if c.manifest is None and self.orphan_bytes+len(body)>MAX_ORPHAN:
                self.retire(frame,'orphan_cap',now); return None
            c.fragments[key]=value
        if self.buffered>MAX_BUFFERED:
            self.retire(frame,'buffer_cap',now); return None
        self.highwater_bytes=max(self.highwater_bytes,self.buffered)
        self.highwater_candidates=max(self.highwater_candidates,len(self.pending))
        if c.manifest is None: return None
        required=sum(math.ceil(d[2]/PAYLOAD) for d in c.desc.values())
        if len(c.fragments)!=required: return None
        decoded={}
        for cid,(eye,offset,length,checksum) in c.desc.items():
            n=math.ceil(length/PAYLOAD)
            if any((cid,i) not in c.fragments for i in range(n)): return None
            data=b''.join(c.fragments[cid,i][1] for i in range(n))
            if crc(data)!=checksum:
                self.retire(frame,'chunk_crc',now); return None
            decoded[cid]=data
        # Both eyes and all chunks passed before the single publication assignment.
        eyes=[b''.join(decoded[cid] for cid,d in c.desc.items() if d[0]==eye) for eye in (0,1)]
        published_now=max(now,self.clock())
        if published_now >= c.deadline:
            self.retire(frame,'assembly_expired',published_now); return None
        pair=Pair(frame,warp,self.session_epoch,self.config_epoch,*eyes,published_now,c.deadline)
        self.published=pair
        self.tickets={key:deadline for key,deadline in self.tickets.items() if key[0]>frame}
        for f in list(self.pending):
            if f<=frame: self.pending.pop(f)
        self.event('published_pair',published_now,frame)
        return pair


def accounting(packets):
    n=len(packets); payload=sum(map(len,packets))
    return dict(datagrams=n,udp_payload_bytes=payload,application_header_bytes=n*HEADER.size,
                assumed_ipv4_bytes=payload+n*28,assumed_ipv6_bytes=payload+n*48,
                max_udp_payload=max(map(len,packets),default=0),
                excludes='link-layer, ACK, congestion, encryption, kernel overhead')
