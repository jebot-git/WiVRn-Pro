# SPDX-License-Identifier: MIT
import unittest
from transport import Receiver, packetize, HEADER, PAYLOAD


class TransportTests(unittest.TestCase):
    def test_reorder_duplicate_stereo(self):
        r = Receiver()
        source = b"abc" * 4000
        for eye in (1, 0):
            for p in reversed(list(packetize(source, 12, eye, 5000))):
                self.assertTrue(r.push(p, 100))
                self.assertTrue(r.push(p, 100))
        self.assertEqual(r.take_stereo(4999), (12, (source, source)))
        self.assertIsNone(r.take_stereo(4999))
        self.assertFalse(r.push(next(packetize(source, 12, 0, 5000)), 100))

    def test_loss_expires_without_retransmission_or_resurrection(self):
        r = Receiver()
        packets = list(packetize(b"a" * 4000, 1, 0, 1000))
        for p in packets[:-1]:
            r.push(p, 100)
        self.assertIsNone(r.take_stereo(1000))
        self.assertEqual(r.eyes, {})
        self.assertFalse(r.push(packets[-1], 1001))

    def test_new_frame_evicts_old_and_rejects_reordered_old(self):
        r = Receiver()
        old = next(packetize(b"x", 1, 0, 1000))
        new = next(packetize(b"y", 2, 1, 1000))
        r.push(old, 10)
        r.push(new, 11)
        self.assertFalse(r.push(old, 12))
        self.assertEqual(set(r.eyes), {1})
        self.assertIsNone(r.take_stereo(13))

    def test_corrupt_and_invalid_packets_do_not_evict(self):
        r = Receiver()
        good = next(packetize(b"x", 1, 0, 1000))
        r.push(good, 10)
        bad = bytearray(next(packetize(b"y", 2, 1, 1000)))
        bad[-1] ^= 1
        for p in (bad, b"", good[:HEADER.size], b"z" * 1201):
            self.assertFalse(r.push(p, 11))
        self.assertEqual(r.newest, 1)

    def test_metadata_conflicts(self):
        r = Receiver()
        r.push(next(packetize(b"a" * 4000, 1, 0, 1000)), 1)
        self.assertFalse(r.push(next(packetize(b"a", 1, 0, 1000)), 2))
        self.assertFalse(r.push(next(packetize(b"a", 1, 1, 1001)), 2))

    def test_bounded_fragment_count(self):
        with self.assertRaises(ValueError):
            list(packetize(b"x" * (PAYLOAD * 4096 + 1), 1, 0, 1000))
        with self.assertRaises(ValueError):
            list(packetize(b"", 1, 0, 1000))


if __name__ == "__main__":
    unittest.main()
