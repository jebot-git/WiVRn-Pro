#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import random
import struct
import unittest
from transport import *

SID=b'test-session-001'; NOW=1_000_000_000

def edit(packet, **changes):
    names='magic version kind flags se ce frame warp obj index count size fg fs fk fr'.split()
    v=list(HEADER.unpack_from(packet))
    for name,value in changes.items(): v[names.index(name)]=value
    return HEADER.pack(*v)+packet[HEADER.size:]

def fixtures(frame=1,warp=10,left=None,right=None,deadline=NOW+50_000_000):
    left=left if left is not None else bytes((i*17)%256 for i in range(19000))
    right=right if right is not None else bytes((255-i*23)%256 for i in range(21000))
    return left,right,packetize(SID,1,1,frame,warp,deadline,left,right)

class Tests(unittest.TestCase):
    def setUp(self): self.r=Receiver(SID, clock=lambda: NOW)
    def fixtures(self, **kwargs):
        deadline=kwargs.get('deadline',NOW+50_000_000)
        if NOW < deadline <= NOW+MAX_FUTURE_NS:
            self.r.issue_deadline(NOW,deadline-NOW,kwargs.get('frame',1),kwargs.get('warp',10))
        return fixtures(**kwargs)
    def deliver(self,packets,now=NOW):
        return [x for p in packets if (x:=self.r.receive(p,now)) is not None]
    def test_exact_pair_and_packet_budget(self):
        l,r,p=self.fixtures(); out=self.deliver(p)
        self.assertEqual(len(out),1); self.assertEqual((out[0].left,out[0].right),(l,r))
        self.assertLessEqual(max(map(len,p)),1200)
    def test_shuffle_orphans_and_duplicate(self):
        l,r,p=self.fixtures(); data=p[1:]*2; random.Random(1408).shuffle(data)
        out=self.deliver(data+p[:1]); self.assertEqual(len(out),1)
        self.assertEqual(out[0].right,r); self.assertGreater(self.r.stats['duplicate'],0)
    def test_missing_fragment_never_publishes(self):
        _,_,p=self.fixtures(); self.assertEqual(self.deliver(p[:-1]),[])
        self.assertIsNone(self.r.published)
    def test_corruption_crc_rejects_entire_pair(self):
        _,_,p=self.fixtures(); p[-1]=p[-1][:-1]+bytes([p[-1][-1]^1])
        self.assertEqual(self.deliver(p),[]); self.assertEqual(self.r.stats['chunk_crc'],1)
    def test_manifest_crc(self):
        _,_,p=self.fixtures(); bad=p[0][:-1]+bytes([p[0][-1]^1]); self.deliver([bad])
        self.assertEqual(self.r.stats['manifest_crc'],1)
    def test_truncation_and_oversize(self):
        _,_,p=self.fixtures(); self.deliver([p[0][:7],p[1][:-1],p[1]+b'x'*1201])
        self.assertEqual(self.r.stats['packet_size'],2); self.assertEqual(self.r.stats['header'],1)
    def test_stale_epoch_and_session(self):
        _,_,p=self.fixtures(); self.deliver([edit(p[0],ce=0),edit(p[0],se=0)])
        wrong=packetize(b'other-session001',1,1,2,20,NOW+5_000_000,b'l',b'r')
        self.deliver(wrong[:1]); self.assertEqual(self.r.stats['epoch'],2)
        self.assertEqual(self.r.stats['session_uuid'],1)
    def test_deadline_admission_and_expiry(self):
        _,_,p=self.fixtures(deadline=NOW+1); self.deliver(p[:2]); self.deliver(p[2:],NOW+1)
        self.assertIsNone(self.r.published); self.assertEqual(self.r.stats['expired'],1)
        self.r=Receiver(SID, clock=lambda: NOW); self.deliver(p,NOW+1); self.assertGreater(self.r.stats['deadline'],0)
    def test_far_future_deadline_rejected(self):
        _,_,p=self.fixtures(deadline=NOW+MAX_FUTURE_NS+1); self.deliver(p[:1])
        self.assertEqual(self.r.stats['deadline'],1)
    def test_warp_mismatch_preserves_candidate(self):
        _,_,p=self.fixtures(); self.deliver(p[:1]+[edit(p[1],warp=99)]+p[1:])
        self.assertEqual(self.r.published.warp,10); self.assertEqual(self.r.stats['warp_conflict'],1)
    def test_published_survives_newer_orphans(self):
        _,_,p=self.fixtures(); self.deliver(p); saved=self.r.published
        for f in range(2,9): self.deliver(self.fixtures(frame=f,warp=f)[2][1:2])
        self.assertIs(self.r.published,saved); self.assertEqual(len(self.r.pending),2)
        self.assertEqual(self.r.stats['superseded_incomplete'],5)
    def test_new_complete_supersedes_old_and_rejects_late(self):
        self.deliver(self.fixtures(frame=1)[2][:-1]); self.deliver(self.fixtures(frame=2)[2])
        self.deliver(self.fixtures(frame=1)[2]); self.assertEqual(self.r.published.frame,2)
        self.assertEqual(len(self.r.pending),0)
    def test_fragment_conflicting_duplicate(self):
        _,_,p=self.fixtures(); self.deliver(p[:2]); bad=p[1][:-1]+bytes([p[1][-1]^1])
        self.deliver([bad]+p[2:]); self.assertEqual(self.r.stats['fragment_conflict'],1)
        self.assertEqual(self.r.published.frame,1)
    def test_orphan_expiration_and_cap(self):
        data=b'x'*(MAX_PAIR//2); _,_,p=self.fixtures(left=data,right=data)
        self.deliver(p[1:]); self.assertEqual(self.r.stats['orphan_cap'],1)
        self.assertLessEqual(self.r.orphan_bytes,MAX_ORPHAN)
        self.r=Receiver(SID, clock=lambda: NOW); self.deliver(p[1:2]); self.r.expire(NOW+ORPHAN_TTL_NS)
        self.assertEqual(len(self.r.pending),0); self.assertEqual(self.r.stats['orphan_expired'],1)
    def test_config_reset_discards_references(self):
        _,_,p=self.fixtures(); self.deliver(p); self.r.configure(SID,1,2,NOW)
        self.assertIsNone(self.r.published); self.deliver(p)
        self.assertEqual(self.r.stats['epoch'],len(p))
    def test_bounded_adversarial_header_counts(self):
        _,_,p=self.fixtures(); self.deliver([edit(p[1],count=65535),edit(p[1],obj=65535),edit(p[1],index=60000)])
        self.assertEqual(self.r.stats['fragment_header'],3); self.assertEqual(self.r.buffered,0)
    def test_manifest_declared_caps_before_allocation(self):
        _,_,p=self.fixtures(); body=bytearray(p[0][HEADER.size:]); struct.pack_into('<I',body,28,MAX_PAIR+1)
        bad=p[0][:HEADER.size]+body; self.deliver([bad]); self.assertEqual(self.r.stats['manifest_bounds'],1)
        self.assertEqual(self.r.buffered,0)
    def test_inconsistent_fragment_size_against_manifest(self):
        _,_,p=self.fixtures(); self.deliver(p[:1]); bad=p[1][:-1]; bad=edit(bad,size=len(bad)-HEADER.size)
        self.deliver([bad]+p[1:]); self.assertEqual(self.r.stats['fragment_length'],1)
        self.assertEqual(self.r.published.frame,1)
    def test_maximum_pair_and_input_cap(self):
        data=b'x'*(MAX_PAIR//2); _,_,p=self.fixtures(left=data,right=data); self.deliver(p)
        self.assertEqual(len(self.r.published.left)+len(self.r.published.right),MAX_PAIR)
        with self.assertRaises(ValueError): packetize(SID,1,1,2,2,NOW+1,data,data+b'x')
        self.assertLessEqual(self.r.highwater_bytes,MAX_BUFFERED)
    def test_each_packet_loss_blocks_publication(self):
        _,_,p=self.fixtures(left=b'L'*1200,right=b'R'*1300)
        for missing in range(len(p)):
            self.r=Receiver(SID, clock=lambda: NOW); self.r.issue_deadline(NOW,50_000_000,1,10); self.deliver(p[:missing]+p[missing+1:])
            self.assertIsNone(self.r.published)
    def test_small_pair_all_permutations(self):
        import itertools
        l,r,p=self.fixtures(left=b'left',right=b'right')
        for ordering in itertools.permutations(p):
            self.r=Receiver(SID, clock=lambda: NOW); self.r.issue_deadline(NOW,50_000_000,1,10); out=self.deliver(ordering)
            self.assertEqual(len(out),1); self.assertEqual((out[0].left,out[0].right),(l,r))
    def test_seeded_malformed_input_keeps_bounds(self):
        rng=random.Random(9221); _,_,p=self.fixtures()
        for i in range(1000):
            packet=bytearray(rng.choice(p))
            for _ in range(1+rng.randrange(4)):
                packet[rng.randrange(len(packet))]=rng.randrange(256)
            if i%7==0: packet=packet[:rng.randrange(len(packet))]
            self.r.receive(bytes(packet),NOW+i)
            self.assertLessEqual(len(self.r.pending),MAX_CANDIDATES)
            self.assertLessEqual(self.r.buffered,MAX_BUFFERED)
            self.assertLessEqual(self.r.orphan_bytes,MAX_ORPHAN)
        self.assertIsNone(self.r.published)
    def test_deadline_ticket_and_accounting(self):
        self.assertEqual(self.r.issue_deadline(NOW,5,99,10),NOW+5)
        with self.assertRaises(ValueError): self.r.issue_deadline(NOW,MAX_FUTURE_NS+1,99,10)
        _,_,p=self.fixtures(); a=accounting(p)
        self.assertEqual(a['assumed_ipv4_bytes'],sum(map(len,p))+len(p)*28)

    def test_unissued_deadline_rejected(self):
        _,_,p=fixtures()
        self.deliver(p)
        self.assertIsNone(self.r.published)
        self.assertEqual(self.r.stats['unissued_deadline'],1)
    def test_ticket_binds_frame_warp_and_exact_deadline(self):
        self.r.issue_deadline(NOW,50_000_000,1,10)
        for kwargs in (dict(frame=2),dict(warp=11),dict(deadline=NOW+49_000_000)):
            _,_,p=fixtures(**kwargs); self.deliver(p[:1])
        self.assertEqual(self.r.stats['unissued_deadline'],3)
        with self.assertRaises(ValueError): self.r.issue_deadline(NOW,49_000_000,1,10)
    def test_ticket_cap_expiry_and_reset(self):
        for frame in range(1,MAX_TICKETS+1): self.r.issue_deadline(NOW,10,frame,10)
        with self.assertRaises(ValueError): self.r.issue_deadline(NOW,10,MAX_TICKETS+1,10)
        self.r.issue_deadline(NOW+10,10,MAX_TICKETS+1,10)
        self.assertEqual(len(self.r.tickets),1)
        self.r.configure(SID,1,2,NOW+11)
        self.assertEqual(self.r.tickets,{})
    def test_uuid_change_requires_fresh_epoch_without_mutating_on_rejection(self):
        _,_,p=self.fixtures(); self.deliver(p); saved=self.r.published
        with self.assertRaises(ValueError): self.r.configure(b'next-session-001',1,2,NOW)
        self.assertIs(self.r.published,saved)
        self.r.configure(b'next-session-001',2,1,NOW)
        self.deliver(p[1:])
        self.assertEqual(self.r.stats['epoch'],len(p)-1)
        self.assertEqual(self.r.pending,{})
    def test_config_epochs_cannot_repeat_regress_or_wrap(self):
        self.r.configure(SID,1,2,NOW)
        for se,ce in ((1,2),(1,1),(0,4),(2**32,1)):
            with self.assertRaises(ValueError): self.r.configure(SID,se,ce,NOW)
        self.r.configure(SID,2,1,NOW)
    def test_assembly_rechecks_clock_and_preserves_previous_complete(self):
        self.deliver(self.fixtures()[2]); saved=self.r.published
        _,_,p=self.fixtures(frame=2,warp=20)
        self.deliver(p[:-1])
        self.r.clock=lambda: NOW+50_000_000
        self.deliver(p[-1:])
        self.assertIs(self.r.published,saved)
        self.assertEqual(self.r.stats['assembly_expired'],1)
        self.assertNotIn(2,self.r.pending)
    def test_publication_uses_fresh_clock_timestamp(self):
        self.r.clock=lambda: NOW+123
        self.deliver(self.fixtures()[2])
        self.assertEqual(self.r.published.published_receiver_ns,NOW+123)
        self.assertEqual(self.r.tickets,{})

if __name__=='__main__':unittest.main(verbosity=2)
