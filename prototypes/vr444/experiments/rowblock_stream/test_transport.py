#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import random
import struct
import unittest
from unittest.mock import patch
import transport as t

SID=b'rowblock-test-v1'
NOW=1_000_000_000


def rrf(width,height,seed=1,mode=0,body=None):
    if body is None:body=bytes((i+seed)%256 for i in range(width*height*3))
    header=bytearray(32);header[:4]=b'RRF1';header[4]=1;header[5]=mode
    struct.pack_into('<IIIII',header,8,width,height,len(body),width*height*3,t.crc(body))
    struct.pack_into('<I',header,28,t.crc(header[:28]));return bytes(header)+body


def pair(width=17,height=33,rows=16):
    return tuple(t.assemble(width,height,rows,[rrf(width,min(rows,height-i*rows),eye+i) for i in range((height+rows-1)//rows)]) for eye in (0,1))


def context(frame=1,warp=10,ttl=100_000_000):
    clock=[NOW];receiver=t.Receiver(SID,clock=lambda:clock[0]);deadline=receiver.issue_deadline(NOW,ttl,frame,warp)
    return receiver,clock,deadline


def run(receiver,clock,records):
    published=[]
    for record in records:
        clock[0]+=1000;value=receiver.receive(record,clock[0])
        if value:published.append(value)
        assert receiver.buffered<=t.MAX_BUFFERED and receiver.orphan_bytes<=t.base.MAX_ORPHAN and len(receiver.pending)<=2
    return published


class RowBlockTransportTests(unittest.TestCase):
    def test_independent_raw_golden_and_roundtrip(self):
        for width,height,rows in ((1,1,16),(17,33,16),(35,129,32),(33,17,128)):
            left,right=pair(width,height,rows);receiver,clock,deadline=context()
            records=t.packetize(SID,1,1,1,10,deadline,left,right)
            values=run(receiver,clock,records);self.assertEqual(len(values),1)
            self.assertEqual((values[0].left,values[0].right),(left,right));self.assertLess(values[0].published_receiver_ns,deadline)
            self.assertTrue(all(len(r)<=1176 for r in records));self.assertEqual(receiver.stats['completed_private_block'],2*((height+rows-1)//rows))

    def test_incremental_sender_needs_no_later_lengths(self):
        receiver,clock,deadline=context();sender=t.Sender(SID,1,1,1,10,deadline,17,33,16)
        left,right=pair();blocks=(t.inspect_rrb(left)[3],t.inspect_rrb(right)[3]);out=[]
        for eye,index in ((1,2),(0,0),(1,0),(0,2),(1,1),(0,1)):
            records=sender.add_block(eye,index,blocks[eye][index]);out+=run(receiver,clock,records)
            if (eye,index)!=(0,1):self.assertFalse(out);self.assertIsNone(receiver.published)
        self.assertTrue(sender.complete);self.assertEqual((out[0].left,out[0].right),(left,right))

    def test_private_blocks_do_not_publish_one_eye(self):
        left,right=pair();receiver,clock,deadline=context();n=3
        records=t.packetize(SID,1,1,1,10,deadline,left,right,[(eye,i) for eye in (0,1) for i in range(n)])
        only_left=[r for r in records if t.HEADER.unpack_from(r)[2]==t.MANIFEST or t.HEADER.unpack_from(r)[3]==0]
        self.assertFalse(run(receiver,clock,only_left));self.assertIsNone(receiver.published)
        self.assertEqual(len(receiver.pending[1].blocks),n)

    def test_reorder_duplicate_and_first_manifest_loss(self):
        left,right=pair()
        for mode in ('shuffle','duplicates','reverse','manifest_loss'):
            receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
            if mode=='shuffle':random.Random(42).shuffle(records)
            elif mode=='duplicates':records=[r for r in records for _ in range(2)]
            elif mode=='reverse':records.reverse()
            else:records=records[1:]
            values=run(receiver,clock,records);self.assertEqual(len(values),1)
            self.assertEqual((values[0].left,values[0].right),(left,right))

    def test_missing_source_never_publishes_and_expires(self):
        left,right=pair();receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        omit=next(i for i,r in enumerate(records) if t.HEADER.unpack_from(r)[2]==t.DATA)
        self.assertFalse(run(receiver,clock,records[:omit]+records[omit+1:]));receiver.expire(deadline)
        self.assertFalse(receiver.pending);self.assertIsNone(receiver.published)

    def test_conflicting_valid_record_retires_whole_candidate(self):
        left,right=pair();receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        first=records[1];fields=list(t.HEADER.unpack_from(first));body=bytearray(first[t.HEADER.size:]);body[-1]^=1
        conflict=t.packet(t.DATA,fields[3],fields[5],fields[6],fields[7],fields[8],fields[9],fields[10],fields[11],fields[12],fields[13],fields[14],body)
        self.assertFalse(run(receiver,clock,[records[0],first,conflict,*records[2:]]));self.assertIn(1,receiver.retired)

    def test_crc_repaired_invalid_inner_grammar_rejects(self):
        for bad in (rrf(1,2,mode=1,body=b'\0\0\0'),rrf(1,1)):
            receiver,clock,deadline=context();sender=t.Sender(SID,1,1,1,10,deadline,1,2,16)
            record=t.packet(t.DATA,0,1,1,1,10,0,1,0,1,len(bad),t.crc(bad),bad)
            self.assertFalse(run(receiver,clock,[sender.manifest,record]));self.assertIn(1,receiver.retired)
            self.assertEqual(receiver.stats['completed_private_block'],0)

    def test_fresh_clock_after_validation_cannot_publish_expired(self):
        left,right=pair(1,1);receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        original=t.inspect_rrf
        def delayed(*args):
            original(*args);clock[0]=deadline
        with patch.object(t,'inspect_rrf',side_effect=delayed):self.assertFalse(run(receiver,clock,records))
        self.assertIsNone(receiver.published)

    def test_fresh_clock_after_stereo_assembly_cannot_publish_expired(self):
        left,right=pair(1,1);receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        original=t.assemble
        def delayed(*args):
            result=original(*args);clock[0]=deadline;return result
        with patch.object(t,'assemble',side_effect=delayed):self.assertFalse(run(receiver,clock,records))
        self.assertIsNone(receiver.published);self.assertIn(1,receiver.retired)

    def test_maximum_block_count_complete_pair(self):
        left,right=pair(1,4096,16);receiver,clock,deadline=context()
        records=t.packetize(SID,1,1,1,10,deadline,left,right)
        values=run(receiver,clock,records);self.assertEqual(len(values),1)
        self.assertEqual((values[0].left,values[0].right),(left,right))
        self.assertEqual(receiver.block_highwater,512)

    def test_valid_conflicting_manifest_retires_and_cannot_resurrect(self):
        receiver,clock,deadline=context();left,right=pair()
        records=t.packetize(SID,1,1,1,10,deadline,left,right)
        other=t.Sender(SID,1,1,1,10,deadline,18,33,16).manifest
        self.assertFalse(run(receiver,clock,[records[0],other,*records[1:]]))
        self.assertIn(1,receiver.retired);self.assertIsNone(receiver.published)

    def test_orphan_expiry_prevents_late_manifest_resurrection(self):
        receiver,clock,deadline=context();left,right=pair()
        records=t.packetize(SID,1,1,1,10,deadline,left,right)
        self.assertFalse(run(receiver,clock,[records[1]]))
        clock[0]+=t.base.ORPHAN_TTL_NS;receiver.expire(clock[0])
        self.assertFalse(run(receiver,clock,records));self.assertIn(1,receiver.retired)

    def test_issued_ticket_session_epoch_and_warp(self):
        left,right=pair(1,1)
        for kind in ('unissued','UUID','epoch','warp'):
            receiver,clock,deadline=context();session=SID if kind!='UUID' else b'other-session-v1'
            records=t.packetize(session,2 if kind=='epoch' else 1,1,1,11 if kind=='warp' else 10,deadline,left,right)
            if kind=='unissued':receiver.tickets.clear()
            self.assertFalse(run(receiver,clock,records));self.assertIsNone(receiver.published)

    def test_two_candidate_bound_and_configuration_reset(self):
        receiver,clock,_=context();left,right=pair(1,1)
        for frame in (1,2,3):
            deadline=receiver.issue_deadline(NOW,100_000_000,frame,10)
            record=t.packetize(SID,1,1,frame,10,deadline,left,right)[0];run(receiver,clock,[record])
        self.assertEqual(set(receiver.pending),{2,3});self.assertIn(1,receiver.retired)
        receiver.configure(SID,1,2,clock[0]);self.assertFalse(receiver.pending);self.assertFalse(receiver.tickets)

    def test_declared_size_reservation_before_allocation(self):
        receiver,clock,deadline=context();sender=t.Sender(SID,1,1,1,10,deadline,1024,128,128)
        n=131050;count=(n+t.SHARD-1)//t.SHARD
        records=[sender.manifest]+[t.packet(t.DATA,eye,1,1,1,10,0,1,0,count,n,0,b'\0'*t.SHARD) for eye in (0,1)]
        self.assertFalse(run(receiver,clock,records));self.assertIn(1,receiver.retired)
        self.assertEqual(receiver.stats['declared stereo cap'],1);self.assertLessEqual(receiver.highwater_bytes,40+t.SHARD)

    def test_orphan_payload_bound(self):
        receiver,clock,_=context();length=200000;count=(length+t.SHARD-1)//t.SHARD
        records=[t.packet(t.DATA,0,1,1,1,10,0,1,i,count,length,0,b'\0'*min(t.SHARD,length-i*t.SHARD)) for i in range(60)]
        self.assertFalse(run(receiver,clock,records));self.assertIn(1,receiver.retired)
        self.assertEqual(receiver.stats['orphan cap'],1);self.assertLessEqual(receiver.highwater_bytes,t.base.MAX_ORPHAN)

    def test_stream_sender_abort_is_permanent(self):
        sender=t.Sender(SID,1,1,1,10,NOW+1000000,256,256,128)
        sender.add_block(0,0,rrf(256,128))
        sender.add_block(1,0,rrf(256,128))
        with self.assertRaises(ValueError):sender.add_block(0,1,rrf(256,128))
        self.assertFalse(sender.complete)
        with self.assertRaises(ValueError):sender.add_block(1,1,rrf(256,128))

    def test_duplicate_source_block_aborts_sender(self):
        sender=t.Sender(SID,1,1,1,10,NOW+1000000,1,1,16);block=rrf(1,1)
        sender.add_block(0,0,block)
        with self.assertRaises(ValueError):sender.add_block(0,0,block)
        with self.assertRaises(ValueError):sender.add_block(1,0,block)

    def test_truncation_and_header_crc_mutations_never_change_pixels(self):
        left,right=pair();receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        for n in range(len(records[0])):self.assertIsNone(receiver.receive(records[0][:n],NOW))
        rng=random.Random(11)
        for _ in range(500):
            receiver,clock,deadline=context();changed=list(records);i=rng.randrange(len(changed));wire=bytearray(changed[i]);wire[rng.randrange(len(wire))]^=1<<rng.randrange(8);changed[i]=bytes(wire)
            result=run(receiver,clock,changed)
            if result:self.assertEqual((result[0].left,result[0].right),(left,right))

    def test_sender_and_rrb_shape_bounds(self):
        for args in ((0,1,16),(1,0,16),(4097,1,16),(4096,4096,16),(1,1,8),(True,1,16)):
            with self.assertRaises(ValueError):t.shape(*args)
        left,right=pair();bad=bytearray(left);bad[32]^=1
        with self.assertRaises(ValueError):t.inspect_rrb(bad)
        with self.assertRaises(ValueError):t.packetize(SID,1,1,1,10,NOW+1000000,left,pair(17,34)[1])
        with self.assertRaises(ValueError):t.packetize(SID,1,1,1,10,NOW+1000000,left,right,[(0,0)])


if __name__=='__main__':unittest.main()
