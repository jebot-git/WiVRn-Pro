#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import random
import struct
import unittest
from unittest.mock import patch
import transport as t

SID=b'vxp-test-session'
NOW=1_000_000_000

def rrf(width,height,seed=0):
    body=bytes((i+seed)%256 for i in range(width*height*3));header=bytearray(32)
    header[:4]=b'RRF1';header[4]=1
    struct.pack_into('<IIIII',header,8,width,height,len(body),width*height*3,t.crc(body))
    struct.pack_into('<I',header,28,t.crc(header[:28]));return bytes(header)+body

def pair(width=64,height=33,rows=16):
    return tuple(t.assemble(width,height,rows,[rrf(width,min(rows,height-i*rows),eye+i) for i in range((height+rows-1)//rows)]) for eye in (0,1))

def context():
    clock=[NOW];receiver=t.Receiver(SID,clock=lambda:clock[0]);deadline=receiver.issue_deadline(NOW,100_000_000,1,10)
    return receiver,clock,deadline

def run(receiver,clock,records):
    values=[]
    for record in records:
        clock[0]+=1000;value=receiver.receive(record,clock[0])
        if value:values.append(value)
        assert receiver.buffered<=t.MAX_BUFFERED and receiver.orphan_bytes<=t.base.MAX_ORPHAN and len(receiver.pending)<=2
    return values

def fields(record):return t.HEADER.unpack_from(record)

def repaired_header(record,**changes):
    f=list(fields(record));body=record[t.HEADER.size:]
    for key,value in changes.items():f[int(key)]=value
    return t.packet(f[2],f[3],f[5],f[6],f[7],f[8],f[9],f[10],f[11],f[12],f[13],f[14],body,f[4])

class StreamFECTests(unittest.TestCase):
    def test_clean_odd_shapes_and_all_group_sizes(self):
        for dims in ((1,1,16),(17,33,16),(35,129,32),(33,17,128),(1,4096,16)):
            left,right=pair(*dims)
            for k in (4,8,16):
                receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right,k=k)
                values=run(receiver,clock,records);self.assertEqual(len(values),1)
                self.assertEqual((values[0].left,values[0].right),(left,right));self.assertTrue(all(len(p)<=1176 for p in records))

    def test_every_single_source_loss_is_exact(self):
        left,right=pair(128,32,32)
        for k in (4,8,16):
            _,_,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right,k=k)
            positions=[i for i,p in enumerate(records) if fields(p)[2]==t.DATA]
            for omitted in positions:
                receiver,clock,_=context();values=run(receiver,clock,[p for i,p in enumerate(records) if i!=omitted])
                self.assertEqual(len(values),1);self.assertEqual((values[0].left,values[0].right),(left,right))
                self.assertEqual(receiver.stats['recovered_private_fragment'],1)

    def test_one_loss_per_group_recovers_in_both_eyes(self):
        left,right=pair(128,64,32)
        for k in (4,8,16):
            receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right,k=k)
            lost=[p for p in records if fields(p)[2]!=t.DATA or fields(p)[11]%k!=0]
            values=run(receiver,clock,lost);self.assertEqual((values[0].left,values[0].right),(left,right))
            self.assertGreater(receiver.stats['recovered_private_fragment'],1)

    def test_two_source_losses_in_one_group_withhold(self):
        left,right=pair(128,32,32)
        for k in (4,8,16):
            receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right,k=k)
            remaining=[p for p in records if not(fields(p)[2]==t.DATA and fields(p)[3]==0 and fields(p)[11] in (0,1))]
            self.assertFalse(run(receiver,clock,remaining));receiver.expire(deadline)
            self.assertFalse(receiver.pending);self.assertIsNone(receiver.published)

    def test_source_and_its_parity_loss_withhold(self):
        left,right=pair();receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        remaining=[p for p in records if not(fields(p)[2] in (t.DATA,t.PARITY) and fields(p)[3]==0 and fields(p)[9]==0 and fields(p)[11]==0)]
        self.assertFalse(run(receiver,clock,remaining));self.assertIsNone(receiver.published)

    def test_only_parity_can_recover_single_shard_blocks(self):
        left,right=pair(1,33,16);receiver,clock,deadline=context()
        records=t.packetize(SID,1,1,1,10,deadline,left,right)
        values=run(receiver,clock,[p for p in records if fields(p)[2]!=t.DATA])
        self.assertEqual((values[0].left,values[0].right),(left,right));self.assertEqual(receiver.stats['recovered_private_fragment'],6)

    def test_first_manifest_loss_and_reordered_repair(self):
        left,right=pair()
        for mode in ('manifest_loss','reverse','shuffle','duplicates'):
            receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
            if mode=='manifest_loss':records=records[1:]
            elif mode=='reverse':records.reverse()
            elif mode=='shuffle':random.Random(18).shuffle(records)
            else:records=[p for p in records for _ in range(2)]
            values=run(receiver,clock,records);self.assertEqual(len(values),1);self.assertEqual((values[0].left,values[0].right),(left,right))

    def test_crc_valid_wrong_parity_cannot_publish_repaired_pixels(self):
        left,right=pair();receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        changed=[]
        for p in records:
            f=fields(p)
            if f[3]==0 and f[9]==0 and f[11]==0:
                if f[2]==t.DATA:continue
                if f[2]==t.PARITY:
                    body=bytearray(p[t.HEADER.size:]);body[-1]^=1
                    p=t.packet(t.PARITY,f[3],f[5],f[6],f[7],f[8],f[9],f[10],f[11],f[12],f[13],f[14],body,f[4])
            changed.append(p)
        self.assertFalse(run(receiver,clock,changed));self.assertIn(1,receiver.retired)

    def test_padding_beyond_recovered_short_tail_must_be_zero(self):
        left,right=pair(128,16,16);receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right,k=8)
        changed=[]
        for p in records:
            f=fields(p)
            if f[3]==0 and f[2]==t.DATA and f[11]+1==f[12]:continue
            if f[3]==0 and f[2]==t.PARITY:
                body=bytearray(p[t.HEADER.size:]);body[-1]^=1
                p=t.packet(t.PARITY,f[3],f[5],f[6],f[7],f[8],f[9],f[10],f[11],f[12],f[13],f[14],body,f[4])
            changed.append(p)
        self.assertFalse(run(receiver,clock,changed));self.assertEqual(receiver.stats['recovered tail padding'],1)

    def test_fec_group_conflict_retires_candidate(self):
        left,right=pair();receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right,k=8)
        changed=list(records);changed[1]=repaired_header(changed[1],**{'4':4})
        self.assertFalse(run(receiver,clock,changed));self.assertEqual(receiver.stats['FEC group conflict'],1)

    def test_old_vxb_magic_is_rejected(self):
        left,right=pair();receiver,clock,deadline=context();old=t.vxb.packetize(SID,1,1,1,10,deadline,left,right)
        self.assertFalse(run(receiver,clock,old));self.assertFalse(receiver.pending)

    def test_late_repair_never_publishes(self):
        left,right=pair(1,1);receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        repairs=[p for p in records if fields(p)[2]==t.PARITY]
        run(receiver,clock,[p for p in records if fields(p)[2]==t.MANIFEST]);clock[0]=deadline
        self.assertFalse(run(receiver,clock,repairs));self.assertIsNone(receiver.published)

    def test_fresh_clock_expiring_during_repair_is_checked(self):
        left,right=pair(1,1);receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        original=t.vxb.inspect_rrf
        def delayed(*args):original(*args);clock[0]=deadline
        with patch.object(t.vxb,'inspect_rrf',side_effect=delayed):
            self.assertFalse(run(receiver,clock,[p for p in records if fields(p)[2]!=t.DATA]))
        self.assertIsNone(receiver.published)

    def test_repair_orphans_are_in_global_payload_bound(self):
        receiver,clock,_=context();length=200000;shards=(length+t.SHARD-1)//t.SHARD
        records=[]
        for eye in (0,1):
            for group in range((shards+3)//4):
                records.append(t.packet(t.PARITY,eye,1,1,1,10,0,1,group,shards,length,0,b'\0'*t.parity_size(length,4,group),4))
        self.assertFalse(run(receiver,clock,records));self.assertLessEqual(receiver.highwater_bytes,t.base.MAX_ORPHAN)
        self.assertIn(1,receiver.retired)

    def test_invalid_group_sizes_and_parity_descriptor(self):
        left,right=pair()
        for k in (0,1,2,3,5,32,True):
            with self.assertRaises(ValueError):t.packetize(SID,1,1,1,10,NOW+1000000,left,right,k=k)
        receiver,clock,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        parity=next(p for p in records if fields(p)[2]==t.PARITY)
        bad=repaired_header(parity,**{'11':65535})
        self.assertFalse(run(receiver,clock,[records[0],bad]));self.assertEqual(receiver.stats['parity size'],1)

    def test_mutations_either_withhold_or_preserve_exact_pair(self):
        left,right=pair();rng=random.Random(71)
        _,_,deadline=context();records=t.packetize(SID,1,1,1,10,deadline,left,right)
        for _ in range(500):
            receiver,clock,_=context();changed=list(records);i=rng.randrange(len(changed));p=bytearray(changed[i]);p[rng.randrange(len(p))]^=1<<rng.randrange(8);changed[i]=bytes(p)
            values=run(receiver,clock,changed)
            if values:self.assertEqual((values[0].left,values[0].right),(left,right))

if __name__=='__main__':unittest.main()
