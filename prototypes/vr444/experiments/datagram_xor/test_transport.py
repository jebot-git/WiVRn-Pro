#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import itertools
import random
import struct
import unittest
from transport import *

SID = b"xor-test-session"
NOW = 1_000_000_000


def change(p, body=None, **changes):
    names = "magic version kind flags se ce frame warp reserved index count size crc".split()
    fields = list(HEADER.unpack_from(p))
    for name, value in changes.items():
        fields[names.index(name)] = value
    if body is None:
        body = p[HEADER.size:]
    fields[-2] = len(body)
    fields[-1] = 0
    header = HEADER.pack(*fields)
    fields[-1] = crc(header[:40] + body)
    return HEADER.pack(*fields) + body


def kind(p):
    return HEADER.unpack_from(p)[2]


class Tests(unittest.TestCase):
    def setUp(self):
        self.now = NOW
        self.r = Receiver(SID, clock=lambda: self.now)

    def fixture(self, frame=1, warp=10, left_size=19000, right_size=21000, width=8, ttl=50_000_000):
        left = bytes((i * 17) % 256 for i in range(left_size))
        right = bytes((255 - i * 23) % 256 for i in range(right_size))
        deadline = self.r.issue_deadline(NOW, ttl, frame, warp)
        return left, right, packetize(SID, 1, 1, frame, warp, deadline, left, right, width)

    def deliver(self, packets, now=None):
        if now is not None:
            self.now = now
        return [result for p in packets if (result := self.r.receive(p, self.now)) is not None]

    def assert_bounds(self):
        self.assertLessEqual(len(self.r.pending), MAX_CANDIDATES)
        self.assertLessEqual(self.r.buffered, MAX_BUFFERED)
        self.assertLessEqual(self.r.orphan_bytes, MAX_ORPHAN)
        self.assertLessEqual(len(self.r.tickets), base.MAX_TICKETS)
        for c in self.r.pending.values():
            self.assertLessEqual(len(c.data), MAX_SHARDS)
            self.assertLessEqual(len(c.parity), MAX_GROUPS)

    def test_exact_pair_and_packet_budget(self):
        for width in (0, 4, 8, 16):
            self.setUp()
            left, right, packets = self.fixture(width=width)
            out = self.deliver(packets)
            self.assertEqual(len(out), 1)
            self.assertEqual((out[0].left, out[0].right), (left, right))
            self.assertLessEqual(max(map(len, packets)), 1200)
            self.assert_bounds()

    def test_each_single_packet_loss(self):
        for width in (4, 8, 16):
            _, _, packets = self.fixture(width=width, left_size=5000, right_size=6000)
            for lost in range(len(packets)):
                self.setUp()
                left, right, packets = self.fixture(width=width, left_size=5000, right_size=6000)
                out = self.deliver(packets[:lost] + packets[lost + 1:])
                self.assertEqual(len(out), 1, (width, lost))
                self.assertEqual((out[0].left, out[0].right), (left, right))

    def test_one_loss_per_stripe_recovers(self):
        left, right, packets = self.fixture(width=4)
        survivors = [p for p in packets if not (kind(p) == DATA and HEADER.unpack_from(p)[9] % 4 == 1)]
        random.Random(1408).shuffle(survivors)
        self.assertEqual(len(self.deliver(survivors)), 1)
        self.assertEqual((self.r.published.left, self.r.published.right), (left, right))
        self.assertGreater(self.r.stats["recovered_source_shards"], 1)

    def test_two_losses_same_stripe_never_publish(self):
        _, _, packets = self.fixture(width=4)
        survivors = [p for p in packets if not (kind(p) == DATA and HEADER.unpack_from(p)[9] in (0, 1))]
        self.assertEqual(self.deliver(survivors), [])
        self.assertIsNone(self.r.published)

    def test_missing_source_and_its_parity_never_publish(self):
        _, _, packets = self.fixture(width=4)
        self.assertEqual(self.deliver([p for p in packets if not (kind(p) in (DATA, PARITY) and HEADER.unpack_from(p)[9] == 0)]), [])

    def test_no_fec_missing_source(self):
        _, _, packets = self.fixture(width=0)
        self.assertEqual(self.deliver(packets[:1] + packets[2:]), [])

    def test_both_manifests_lost(self):
        _, _, packets = self.fixture()
        self.assertEqual(self.deliver([p for p in packets if kind(p) != MANIFEST]), [])
        self.assert_bounds()

    def test_all_small_permutations(self):
        _, _, packets = self.fixture(left_size=3, right_size=4)
        for ordering in itertools.permutations(packets):
            self.setUp()
            left, right, _ = self.fixture(left_size=3, right_size=4)
            self.assertEqual(len(self.deliver(ordering)), 1)
            self.assertEqual((self.r.published.left, self.r.published.right), (left, right))

    def test_shuffle_duplicate_and_conflict_preserves_data(self):
        left, right, packets = self.fixture()
        self.deliver(packets[:2])
        bad = change(packets[1], body=bytes([packets[1][HEADER.size] ^ 1]) + packets[1][HEADER.size + 1:])
        self.deliver([bad])
        rest = packets[1:] * 2
        random.Random(3).shuffle(rest)
        self.assertEqual(len(self.deliver(rest)), 1)
        self.assertEqual(self.r.published.left, left)
        self.assertEqual(self.r.published.right, right)
        self.assertEqual(self.r.stats["fragment_conflict"], 1)

    def test_crc_bad_packet_can_be_recovered(self):
        left, right, packets = self.fixture()
        bad = packets[1][:-1] + bytes([packets[1][-1] ^ 1])
        out = self.deliver(packets[:1] + [bad] + packets[2:])
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0].left, out[0].right), (left, right))
        self.assertEqual(self.r.stats["packet_crc"], 1)

    def test_forged_parity_cannot_bypass_final_eye_crc(self):
        _, _, packets = self.fixture(width=4)
        delivered = []
        for p in packets:
            index = HEADER.unpack_from(p)[9]
            if kind(p) == DATA and index == 0:
                continue
            if kind(p) == PARITY and index == 0:
                p = change(p, body=bytes([p[HEADER.size] ^ 1]) + p[HEADER.size + 1:])
            delivered.append(p)
        self.assertEqual(self.deliver(delivered), [])
        self.assertEqual(self.r.stats["stereo_crc"], 1)

    def test_nonzero_recovered_tail_rejected(self):
        _, _, packets = self.fixture(left_size=3, right_size=4)
        parity = next(p for p in packets if kind(p) == PARITY)
        bad = change(parity, body=parity[HEADER.size:-1] + b"\x01")
        self.assertEqual(self.deliver([packets[0], bad]), [])
        self.assertEqual(self.r.stats["parity_padding"], 1)

    def test_deadline_stereo_atomic_and_previous_retained(self):
        left, right, packets = self.fixture()
        self.deliver(packets)
        saved = self.r.published
        _, _, second = self.fixture(frame=2, warp=20, ttl=5_000_000)
        self.deliver(second[:2])
        self.assertIs(self.r.published, saved)
        self.deliver(second[2:], NOW + 5_000_000)
        self.assertIs(self.r.published, saved)
        self.assertEqual((saved.left, saved.right), (left, right))

    def test_clock_rechecked_after_recovery_and_crc(self):
        _, _, packets = self.fixture(ttl=1_000_000)
        self.r.clock = lambda: NOW + 1_000_000
        self.assertEqual(self.deliver(packets), [])
        self.assertEqual(self.r.stats["assembly_expired"], 1)

    def test_stale_epochs_uuid_and_config_reset(self):
        _, _, packets = self.fixture()
        self.deliver(packets)
        self.r.configure(SID, 1, 2, NOW)
        self.assertIsNone(self.r.published)
        self.assertEqual(self.r.tickets, {})
        self.deliver(packets)
        self.assertEqual(self.r.stats["epoch"], len(packets))
        with self.assertRaises(ValueError):
            self.r.configure(b"new-xor-session!", 1, 3, NOW)
        with self.assertRaises(ValueError):
            self.r.configure(SID, 1, 2, NOW)

    def test_unissued_and_modified_ticket(self):
        packets = packetize(SID, 1, 1, 1, 10, NOW + 50, b"l", b"r")
        self.assertEqual(self.deliver(packets), [])
        self.assertEqual(self.r.buffered, 0)
        self.r.issue_deadline(NOW, 60, 1, 10)
        self.deliver(packets[:1])
        self.assertEqual(self.r.stats["unissued_deadline"], 1)

    def test_candidate_and_orphan_caps(self):
        for frame in range(1, 20):
            _, _, packets = self.fixture(frame=frame, warp=frame)
            self.deliver([p for p in packets if kind(p) == DATA][:3])
            self.assert_bounds()
        self.r.expire(NOW + base.ORPHAN_TTL_NS)
        self.assertEqual(self.r.buffered, 0)
        self.setUp()
        _, _, packets = self.fixture(left_size=MAX_PAIR // 2, right_size=MAX_PAIR // 2)
        self.deliver([p for p in packets if kind(p) != MANIFEST])
        self.assertEqual(self.r.stats["orphan_cap"], 1)
        self.assert_bounds()

    def test_maximum_pair_and_recovery(self):
        left, right, packets = self.fixture(left_size=MAX_PAIR // 2, right_size=MAX_PAIR // 2, width=4)
        self.deliver([p for p in packets if not (kind(p) == DATA and HEADER.unpack_from(p)[9] % 4 == 0)])
        self.assertEqual((self.r.published.left, self.r.published.right), (left, right))
        self.assert_bounds()
        self.assertLessEqual(self.r.highwater_bytes, MAX_BUFFERED)
        with self.assertRaises(ValueError):
            packetize(SID, 1, 1, 2, 20, NOW + 50, left, right + b"x")

    def test_maximum_pair_first_manifest_loss(self):
        for width in (0, 4, 8, 16):
            self.setUp()
            left, right, packets = self.fixture(left_size=MAX_PAIR // 2, right_size=MAX_PAIR // 2, width=width)
            self.assertEqual(len(self.deliver(packets[1:])), 1)
            self.assertEqual((self.r.published.left, self.r.published.right), (left, right))

    def test_semantic_header_and_manifest_rejection(self):
        _, _, packets = self.fixture()
        for fields in ({"version": 2}, {"flags": 1}, {"reserved": 1}, {"kind": 7}, {"frame": 0},
                       {"count": 65535}, {"index": 65535}):
            self.deliver([change(packets[1], **fields)])
            self.assertEqual(self.r.buffered, 0)
        for offset, value in ((24, MAX_PAIR + 1), (28, 0)):
            body = bytearray(packets[0][HEADER.size:])
            struct.pack_into("<I", body, offset, value)
            self.deliver([change(packets[0], body=bytes(body))])
            self.assertEqual(self.r.buffered, 0)
        for offset, value in ((40, 3), (41, 1), (42, 255)):
            body = bytearray(packets[0][HEADER.size:])
            body[offset] = value
            self.deliver([change(packets[0], body=bytes(body))])
            self.assertEqual(self.r.buffered, 0)

    def test_orphan_geometry_rejected_after_manifest(self):
        _, _, packets = self.fixture()
        bad = change(packets[1], body=packets[1][HEADER.size:-1])
        self.deliver([bad, packets[0]])
        self.assertEqual(self.r.stats["orphan_manifest_conflict"], 1)
        self.assertIsNone(self.r.published)

    def test_superseded_newer_complete_and_late_old(self):
        _, _, old = self.fixture()
        self.deliver(old[:2])
        _, _, new = self.fixture(frame=2, warp=20)
        self.deliver(new)
        saved = self.r.published
        self.deliver(old)
        self.assertIs(self.r.published, saved)
        self.assertEqual(saved.frame, 2)

    def test_every_truncation_and_seeded_malformed_bounds(self):
        _, _, packets = self.fixture()
        for p in packets[:3]:
            for cut in range(len(p)):
                self.r.receive(p[:cut], NOW)
                self.assert_bounds()
        rng = random.Random(9221)
        for i in range(10000):
            self.setUp()
            _, _, packets = self.fixture(left_size=300, right_size=500)
            p = bytearray(rng.choice(packets))
            for _ in range(1 + rng.randrange(4)):
                p[rng.randrange(len(p))] ^= 1 << rng.randrange(8)
            if i % 2 == 0 and len(p) > HEADER.size:
                p = change(bytes(p))  # CRC-valid mutations reach semantic validation.
            self.r.receive(bytes(p), NOW)
            self.assert_bounds()
            if self.r.published is not None:
                self.assertEqual(len(self.r.published.left), 300)
                self.assertEqual(len(self.r.published.right), 500)

    def test_accounting(self):
        _, _, packets = self.fixture()
        totals = accounting(packets)
        self.assertEqual(totals["kinds"]["manifest"], 2)
        self.assertEqual(totals["assumed_ipv6_bytes"], sum(map(len, packets)) + 48 * len(packets))
        self.assertLessEqual(totals["maximum_udp_payload"], 1200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
