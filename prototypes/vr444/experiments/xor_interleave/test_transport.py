#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import math
import random
import unittest
from transport import *

SID = b"interleave-test!"
NOW = 1_000_000_000


def identity(p):
    fields = HEADER.unpack_from(p)
    return fields[2], fields[9]


def changed(p, **updates):
    fields = list(HEADER.unpack_from(p))
    names = "magic version kind flags se ce frame warp reserved index count size crc".split()
    for key, value in updates.items():
        fields[names.index(key)] = value
    fields[-1] = 0
    prefix = HEADER.pack(*fields)
    return prefix[:40] + struct.pack("<I", crc(prefix[:40] + p[HEADER.size:])) + p[HEADER.size:]


class Tests(unittest.TestCase):
    def fixture(self, size=40000, width=8, module=None):
        module = sys.modules[Receiver.__module__] if module is None else module
        rng = random.Random(size)
        self.left = rng.randbytes(size // 3)
        self.right = rng.randbytes(size - len(self.left))
        self.now = NOW
        self.receiver = module.Receiver(SID, clock=lambda: self.now)
        self.deadline = self.receiver.issue_deadline(NOW, 50_000_000, 1, 10)
        return module.packetize(SID, 1, 1, 1, 10, self.deadline, self.left, self.right, width)

    def deliver(self, packets):
        published = []
        for p in packets:
            result = self.receiver.receive(p, self.now)
            if result:
                self.assertEqual((result.left, result.right), (self.left, self.right))
                self.assertLess(result.published_receiver_ns, self.deadline)
                published.append(result)
            self.assertLessEqual(len(self.receiver.pending), MAX_CANDIDATES)
            self.assertLessEqual(self.receiver.buffered, MAX_BUFFERED)
            self.assertLessEqual(self.receiver.orphan_bytes, MAX_ORPHAN)
            self.assertLessEqual(len(self.receiver.tickets), base.MAX_TICKETS)
        self.assertLessEqual(len(published), 1)
        return published

    def test_group_partition_all_geometries_and_burst_proof(self):
        for n in range(1, MAX_SHARDS + 1):
            for width in (4, 8, 16):
                groups = math.ceil(n / width)
                indices = [list(group_indices(g, width, n)) for g in range(groups)]
                self.assertEqual(sorted(i for group in indices for i in group), list(range(n)))
                self.assertTrue(all(1 <= len(group) <= width for group in indices))
                # Every <=G consecutive source-only erasures hits each group
                # at most once. This proves a geometry property, not WiFi loss.
                for start in range(n):
                    erased = range(start, min(start + groups, n))
                    self.assertEqual(len({i % groups for i in erased}), len(erased))

    def test_source_bursts_recover_and_match_accounted_wire(self):
        for size in (3, 2304, 2305, 32768, 65536, MAX_PAIR):
            for width in (4, 8, 16):
                packets = self.fixture(size, width)
                old = xor.packetize(SID, 1, 1, 1, 10, self.deadline, self.left, self.right, width)
                self.assertEqual(accounting(packets), accounting(old))
                n = math.ceil(size / SHARD_BYTES)
                groups = math.ceil(n / width)
                for start in sorted({0, max(0, n//2-groups//2), max(0, n-groups)}):
                    packets = self.fixture(size, width)
                    erased = range(start, min(start+groups, n))
                    survivors = [p for p in packets if not (identity(p)[0] == DATA and identity(p)[1] in erased)]
                    self.assertEqual(len(self.deliver(survivors)), 1, (size, width, start))

    def test_each_single_wire_packet_loss(self):
        for width in (4, 8, 16):
            packets = self.fixture(11000, width)
            for i in range(len(packets)):
                packets = self.fixture(11000, width)
                self.assertEqual(len(self.deliver(packets[:i]+packets[i+1:])), 1)

    def test_periodic_same_group_losses_do_not_recover(self):
        packets = self.fixture()
        groups = math.ceil(math.ceil(40000 / SHARD_BYTES)/8)
        self.assertEqual(self.deliver([p for p in packets if not (identity(p)[0] == DATA and identity(p)[1] in (0, groups))]), [])

    def test_cross_profile_datagrams_rejected_before_candidates(self):
        packets = self.fixture()
        old = xor.Receiver(SID, clock=lambda: NOW)
        old.issue_deadline(NOW, 50_000_000, 1, 10)
        for p in packets:
            self.assertIsNone(old.receive(p, NOW))
        self.assertEqual(old.pending, {})
        old_packets = xor.packetize(SID, 1, 1, 1, 10, self.deadline, self.left, self.right, 8)
        self.assertEqual(self.deliver(old_packets), [])
        self.assertEqual(self.receiver.pending, {})

    def test_manifest_loss_duplicate_reordering_no_fec_and_expiry(self):
        packets = self.fixture(MAX_PAIR)
        self.assertEqual(len(self.deliver(packets[1:])), 1)
        packets = self.fixture(40000)
        duplicated = packets[1:]*2
        random.Random(42).shuffle(duplicated)
        self.assertEqual(len(self.deliver(duplicated)), 1)
        packets = self.fixture(width=0)
        self.assertEqual(self.deliver([p for p in packets if identity(p) != (DATA, 0)]), [])
        packets = self.fixture()
        self.now = self.deadline
        self.assertEqual(self.deliver(packets), [])

    def test_completed_pair_survives_incomplete_expired_successor(self):
        packets = self.fixture()
        self.deliver(packets)
        saved = self.receiver.published
        deadline = self.receiver.issue_deadline(NOW, 5_000_000, 2, 20)
        second = packetize(SID, 1, 1, 2, 20, deadline, self.left, self.right, 8)
        self.deliver(second[:2]);self.now = deadline
        self.assertEqual(self.deliver(second[2:]), [])
        self.assertIs(self.receiver.published, saved)

    def test_crc_geometry_epoch_and_unknown_ticket_reject(self):
        packets = self.fixture()
        manifest = packets[0]
        for kwargs in (dict(version=2), dict(flags=1), dict(reserved=1), dict(se=2), dict(ce=2), dict(frame=2), dict(warp=11), dict(count=2)):
            self.assertEqual(self.deliver([changed(manifest, **kwargs)]), [])
        self.assertEqual(self.receiver.pending, {})
        self.assertEqual(self.deliver([manifest[:-1]+bytes([manifest[-1]^1])]), [])

    def test_fresh_postassembly_clock_blocks_publication(self):
        packets = self.fixture()
        self.receiver.clock = lambda: self.deadline
        self.assertEqual(self.deliver(packets), [])
        self.assertEqual(self.receiver.stats['assembly_expired'], 1)

    def test_crc_repaired_parity_rejects_final_bytes_and_padding(self):
        for corrupt_at in (0, SHARD_BYTES-1):
            packets = self.fixture(2305, 8)
            forged = []
            for p in packets:
                if identity(p) == (DATA, 2):
                    continue
                if identity(p) == (PARITY, 0):
                    body = bytearray(p[HEADER.size:])
                    body[corrupt_at] ^= 1
                    prefix = p[:40]
                    p = prefix + struct.pack('<I', crc(prefix+body)) + body
                forged.append(p)
            self.assertEqual(self.deliver(forged), [])
            self.assertFalse(self.receiver.pending)
            self.assertIsNone(self.receiver.published)

    def test_invalid_geometry_api(self):
        for args in ((0,0,1),(0,8,0),(0,8,MAX_SHARDS+1),(1,8,1)):
            with self.assertRaises(ValueError):group_indices(*args)


if __name__ == "__main__":
    unittest.main()
