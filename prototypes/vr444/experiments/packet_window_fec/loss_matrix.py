#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Matched erased codec-fragment identities, not a physical burst-loss model."""
import transport as t

def study(window_records,block_records,vxp,session,left,right,now=1_000_000_000):
    def identity(record,window):
        if window:
            outer=t.HEADER.unpack_from(record)
            if outer[2]!=t.DATA:return None
            f=t.vxb.HEADER.unpack_from(record,t.HEADER.size)
        else:f=vxp.HEADER.unpack_from(record)
        return (f[3],f[9],f[11]) if f[2]==t.vxb.DATA else None
    window_ids=[identity(p,True) for p in window_records if identity(p,True) is not None]
    block_ids=[identity(p,False) for p in block_records if identity(p,False) is not None]
    t.need(window_ids==block_ids and len(set(window_ids))==len(window_ids),'matched unique codec-fragment sequence required')
    rows=[]
    for length in (1,2,4,8):
        if length>len(window_ids):continue
        for start in sorted({0,(len(window_ids)-length)//2,len(window_ids)-length}):
            erased=set(window_ids[start:start+length]);outcomes={}
            for name,records,window in (('packet_window',window_records,True),('per_block',block_records,False)):
                clock=[now]
                if window:
                    receiver=t.Receiver(session,clock=lambda:clock[0]);receiver.issue(now,100_000_000,1,10)
                else:
                    receiver=vxp.Receiver(session,clock=lambda:clock[0]);receiver.issue_deadline(now,100_000_000,1,10)
                values=[]
                for p in records:
                    if identity(p,window) in erased:continue
                    clock[0]+=1000;value=receiver.receive(p,clock[0])
                    if value:values.append(value)
                    t.need(receiver.buffered<=(t.MAX_BUFFERED if window else vxp.MAX_BUFFERED),'matched-erasure payload cap')
                t.need(len(values)<=1,'repeated stereo publication')
                if values:t.need((values[0].left,values[0].right)==(left,right),'matched-erasure pixels differ')
                else:
                    receiver.expire(now+100_000_000)
                    t.need(not(receiver.frames if window else receiver.pending),'unrecovered candidate did not expire')
                outcomes[name]=bool(values)
            rows.append(dict(erased_codec_sources=length,start_source_index=start,
                erased_identities=[list(key) for key in sorted(erased)],**outcomes))
    return rows
