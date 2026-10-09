#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import random
import struct
import unittest
import importlib.util
from pathlib import Path
import sys
from unittest.mock import patch
import transport as t
import loss_matrix

SID=b'vwg-test-session'
NOW=1_000_000_000

def rrf(width,height,seed=0):
    body=bytes((i+seed)%256 for i in range(width*height*3));header=bytearray(32);header[:4]=b'RRF1';header[4]=1
    struct.pack_into('<IIIII',header,8,width,height,len(body),width*height*3,t.crc(body))
    struct.pack_into('<I',header,28,t.crc(header[:28]));return bytes(header)+body

def pair(width=64,height=33,rows=16):
    return tuple(t.vxb.assemble(width,height,rows,[rrf(width,min(rows,height-i*rows),eye+i) for i in range((height+rows-1)//rows)]) for eye in (0,1))

def context(frame=1):
    clock=[NOW];receiver=t.Receiver(SID,clock=lambda:clock[0]);handle,deadline=receiver.issue(NOW,100_000_000,frame,10)
    return receiver,clock,handle,deadline

def fields(p):return t.HEADER.unpack_from(p)
def inner(p):return t.vxb.HEADER.unpack_from(p,t.HEADER.size)
def source(p):return fields(p)[2]==t.DATA
def codec_source(p):return source(p) and inner(p)[2]==t.vxb.DATA

def run(receiver,clock,records):
    values=[]
    for p in records:
        clock[0]+=1000;value=receiver.receive(p,clock[0])
        if value:values.append(value)
        assert receiver.buffered<=t.MAX_BUFFERED and len(receiver.frames)<=2 and len(receiver.tickets)<=32
        assert receiver.inner.orphan_bytes<=t.base.MAX_ORPHAN
    return values

class PacketWindowTests(unittest.TestCase):
    def test_clean_odd_max_blocks_and_group_sizes(self):
        for dims in ((1,1,16),(17,33,16),(35,129,32),(33,17,128),(1,4096,16)):
            left,right=pair(*dims)
            for k in (4,8,16):
                receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=k)
                values=run(receiver,clock,records);self.assertEqual(len(values),1)
                self.assertEqual((values[0].left,values[0].right),(left,right));self.assertTrue(all(len(p)<=1200 for p in records))

    def test_every_single_source_record_loss_is_exact(self):
        left,right=pair(128,32,32)
        for k in (4,8,16):
            _,_,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=k)
            for omitted in [i for i,p in enumerate(records) if source(p)]:
                receiver,clock,_,_=context();values=run(receiver,clock,[p for i,p in enumerate(records) if i!=omitted])
                self.assertEqual(len(values),1);self.assertEqual((values[0].left,values[0].right),(left,right))

    def test_one_source_record_per_group_recovers(self):
        left,right=pair(128,64,32)
        for k in (4,8,16):
            receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=k)
            values=run(receiver,clock,[p for p in records if not(source(p) and fields(p)[4]==0)])
            self.assertEqual((values[0].left,values[0].right),(left,right));self.assertGreater(receiver.stats['recovered_record'],0)

    def test_two_codec_sources_in_one_group_withhold(self):
        left,right=pair(128,32,32)
        for k in (4,8,16):
            receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=k)
            groups={}
            for i,p in enumerate(records):
                if codec_source(p):groups.setdefault(fields(p)[5],[]).append(i)
            omitted=next(ids[:2] for ids in groups.values() if len(ids)>=2)
            self.assertFalse(run(receiver,clock,[p for i,p in enumerate(records) if i not in omitted]))
            receiver.expire(deadline);self.assertFalse(receiver.frames);self.assertIsNone(receiver.published)

    def test_source_and_matching_parity_loss_withhold(self):
        left,right=pair();receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right)
        omitted=next(i for i,p in enumerate(records) if codec_source(p));group=fields(records[omitted])[5]
        remaining=[p for i,p in enumerate(records) if i!=omitted and not(fields(p)[2]==t.PARITY and fields(p)[5]==group)]
        self.assertFalse(run(receiver,clock,remaining));self.assertIsNone(receiver.published)

    def test_no_parity_reorder_and_duplicates(self):
        left,right=pair(17,33,16)
        for mode in ('no_parity','reverse','shuffle','duplicates'):
            receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right)
            if mode=='no_parity':records=[p for p in records if source(p)]
            elif mode=='reverse':records.reverse()
            elif mode=='shuffle':random.Random(8).shuffle(records)
            else:records=[p for p in records for _ in range(2)]
            values=run(receiver,clock,records);self.assertEqual(len(values),1);self.assertEqual((values[0].left,values[0].right),(left,right))

    def test_sender_emits_before_finish_and_seals_partial_tail(self):
        receiver,clock,handle,deadline=context();sender=t.Sender(handle,SID,1,1,1,10,deadline,1,1,16,k=8)
        first=sender.add_block(0,0,rrf(1,1));self.assertEqual(len(first),3);self.assertTrue(all(source(p) for p in first))
        self.assertFalse(run(receiver,clock,first));last=sender.add_block(1,0,rrf(1,1,1));self.assertEqual(len(last),1)
        # Lose the final source and recover it only when finish seals the window.
        repairs=sender.finish();self.assertEqual(fields(repairs[0])[6],4)
        values=run(receiver,clock,repairs);self.assertEqual((values[0].left,values[0].right),pair(1,1))
        with self.assertRaises(ValueError):sender.finish()
        with self.assertRaises(ValueError):sender.add_block(0,0,rrf(1,1))

    def test_premature_finish_permanently_aborts(self):
        _,_,handle,deadline=context();sender=t.Sender(handle,SID,1,1,1,10,deadline,1,1,16)
        sender.add_block(0,0,rrf(1,1))
        with self.assertRaises(ValueError):sender.finish()
        with self.assertRaises(ValueError):sender.add_block(1,0,rrf(1,1))

    def test_arbitrary_complete_block_order_stays_canonical(self):
        left,right=pair(17,33,16);receiver,clock,handle,deadline=context()
        order=[(1,2),(0,0),(1,0),(0,2),(1,1),(0,1)]
        records=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=4,order=order)
        values=run(receiver,clock,records);self.assertEqual((values[0].left,values[0].right),(left,right))

    def test_one_complete_eye_never_publishes(self):
        receiver,clock,handle,deadline=context();sender=t.Sender(handle,SID,1,1,1,10,deadline,17,33,16,k=4)
        left,_=pair(17,33,16);blocks=t.vxb.inspect_rrb(left)[3]
        for index,block in enumerate(blocks):self.assertFalse(run(receiver,clock,sender.add_block(0,index,block)))
        self.assertIsNone(receiver.published);self.assertEqual(len(receiver.inner.pending[1].blocks),3)

    def test_late_canonical_budget_failure_permanently_aborts(self):
        _,_,handle,deadline=context();sender=t.Sender(handle,SID,1,1,1,10,deadline,256,256,128)
        sender.add_block(0,0,rrf(256,128));sender.add_block(1,0,rrf(256,128))
        with self.assertRaises(ValueError):sender.add_block(0,1,rrf(256,128))
        with self.assertRaises(ValueError):sender.finish()

    def test_final_repair_overlong_zero_padding_rejects_before_publication(self):
        receiver,clock,handle,deadline=context();left,right=pair(1,1);records=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=8)
        omitted=max(i for i,p in enumerate(records) if source(p));changed=[]
        for i,p in enumerate(records):
            if i==omitted:continue
            f=fields(p)
            if f[2]==t.PARITY:p=t.packet(t.PARITY,f[3],f[4],f[5],f[6],f[7],p[t.HEADER.size:]+b'\0')
            changed.append(p)
        self.assertFalse(run(receiver,clock,changed));self.assertIsNone(receiver.published)
        self.assertEqual(receiver.stats['noncanonical parity length'],1)

    def test_crc_valid_bad_repair_never_publishes(self):
        left,right=pair();receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right)
        omitted=next(i for i,p in enumerate(records) if codec_source(p));group=fields(records[omitted])[5];changed=[]
        for i,p in enumerate(records):
            if i==omitted:continue
            f=fields(p)
            if f[2]==t.PARITY and f[5]==group:
                body=bytearray(p[t.HEADER.size:]);body[-1]^=1;p=t.packet(f[2],f[3],f[4],f[5],f[6],f[7],body)
            changed.append(p)
        self.assertFalse(run(receiver,clock,changed));self.assertIsNone(receiver.published)

    def test_inner_identity_must_match_issued_handle(self):
        left,right=pair()
        for args in ((2,1,1,10),(1,2,1,10),(1,1,2,10),(1,1,1,11)):
            receiver,clock,handle,deadline=context()
            se,ce,frame,warp=args;wire=t.vxb.packetize(SID,se,ce,frame,warp,deadline,left,right)[0]
            self.assertIsNone(receiver.receive(t.packet(t.DATA,8,0,0,0,handle,wire),clock[0]))
            self.assertIsNone(receiver.published);self.assertFalse(receiver.frames)
            self.assertEqual(receiver.stats['inner ticket identity'],1)

    def test_unissued_expired_and_old_configuration_handles_reject(self):
        left,right=pair();receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right)
        empty=t.Receiver(SID,clock=lambda:clock[0]);self.assertFalse(run(empty,clock,records));self.assertFalse(empty.frames)
        receiver.expire(deadline);clock[0]=deadline;self.assertFalse(run(receiver,clock,records))
        receiver.configure(SID,1,2,clock[0]);new,_=receiver.issue(clock[0],1000000,2,10)
        self.assertGreater(new,handle);self.assertFalse(run(receiver,clock,records));self.assertIsNone(receiver.published)

    def test_ticket_cap_immutable_deadline_and_types(self):
        receiver,clock,handle,deadline=context();self.assertEqual(receiver.issue(NOW,100000000,1,10),(handle,deadline))
        for args in ((NOW,100000001,1,10),(NOW,True,1,10),(NOW,100000000,1,11),(NOW,999,1,10),(-1,10,2,10),(2**64-1,10,2,10)):
            with self.assertRaises(ValueError):receiver.issue(*args)
        for frame in range(2,33):receiver.issue(NOW,100000000,frame,10)
        with self.assertRaises(ValueError):receiver.issue(NOW,100000000,33,10)

    def test_two_pending_frame_bound(self):
        receiver,clock,_,_=context();left,right=pair()
        for frame in (1,2,3):
            handle,deadline=receiver.issue(NOW,100000000,frame,10)
            records=t.packetize(handle,SID,1,1,frame,10,deadline,left,right)
            self.assertFalse(run(receiver,clock,records[:1]))
        self.assertEqual(len(receiver.frames),2);self.assertIn(1,receiver.inner.retired)

    def test_group_size_slot_and_sealed_count_conflicts(self):
        left,right=pair();receiver,clock,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right)
        first=records[0];f=fields(records[1]);conflict=t.packet(f[2],4,f[4],f[5],f[6],f[7],records[1][t.HEADER.size:])
        self.assertFalse(run(receiver,clock,[first,conflict]));self.assertEqual(receiver.stats['group size conflict'],1)
        receiver,clock,handle,deadline=context();bad=t.packet(t.DATA,4,4,0,0,handle,first[t.HEADER.size:])
        self.assertFalse(run(receiver,clock,[bad]));self.assertEqual(receiver.stats['source slot'],1)

    def test_window_payload_cap_before_storage(self):
        left,right=pair(256,128,128);receiver,clock,handle,deadline=context()
        original=t.vxb.packetize(SID,1,1,1,10,deadline,left,right);manifest=original[0];body=original[1]
        records=[t.packet(t.DATA,4,0,0,0,handle,manifest)]
        for group in range(1,(t.MAX_RECORDS+3)//4):
            for slot in range(3):
                if group*4+slot<t.MAX_RECORDS:records.append(t.packet(t.DATA,4,slot,group,0,handle,body))
        self.assertFalse(run(receiver,clock,records));self.assertEqual(receiver.stats['window payload cap'],1)
        self.assertLessEqual(receiver.highwater,t.MAX_BUFFERED);self.assertIsNone(receiver.published)

    def test_fresh_deadline_during_recovered_decode(self):
        receiver,clock,handle,deadline=context();left,right=pair(1,1);records=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=8)
        omitted=max(i for i,p in enumerate(records) if source(p));original=t.vxb.inspect_rrf
        calls=[0]
        def delayed(*args):
            original(*args);calls[0]+=1
            if calls[0]==2:clock[0]=deadline
        with patch.object(t.vxb,'inspect_rrf',side_effect=delayed):
            self.assertFalse(run(receiver,clock,[p for i,p in enumerate(records) if i!=omitted]))
        self.assertIsNone(receiver.published)
        self.assertEqual(receiver.stats['recovered_record'],1)

    def test_mutations_never_change_successful_stereo(self):
        left,right=pair();_,_,handle,deadline=context();records=t.packetize(handle,SID,1,1,1,10,deadline,left,right);rng=random.Random(48)
        for _ in range(500):
            receiver,clock,_,_=context();changed=list(records);i=rng.randrange(len(changed));p=bytearray(changed[i]);p[rng.randrange(len(p))]^=1<<rng.randrange(8);changed[i]=bytes(p)
            values=run(receiver,clock,changed)
            if values:self.assertEqual((values[0].left,values[0].right),(left,right))

    def test_matched_erasure_matrix_preserves_identity_and_tradeoff(self):
        spec=importlib.util.spec_from_file_location('window_test_block_control',Path(__file__).resolve().parents[1]/'rowblock_stream_fec/transport.py')
        block=importlib.util.module_from_spec(spec);sys.modules[spec.name]=block;spec.loader.exec_module(block)
        left,right=pair(1,33,16);_,_,handle,deadline=context()
        window=t.packetize(handle,SID,1,1,1,10,deadline,left,right,k=8)
        protected=block.packetize(SID,1,1,1,10,deadline,left,right,k=8)
        rows=loss_matrix.study(window,protected,block,SID,left,right)
        self.assertTrue(all(r['packet_window'] and r['per_block'] for r in rows if r['erased_codec_sources']==1))
        self.assertTrue(any(r['per_block'] and not r['packet_window'] for r in rows if r['erased_codec_sources']==2))
        with self.assertRaises(ValueError):loss_matrix.study(window,list(reversed(protected)),block,SID,left,right)

if __name__=='__main__':unittest.main()
