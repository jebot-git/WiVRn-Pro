#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
import unittest
from replay import *


class Tests(unittest.TestCase):
    def test_clean_sustainable_service_completes_every_frame(self):
        for policy in POLICIES:
            result = simulate(policy, rate_mbps=300, frames=30)
            self.assertEqual(result["published_frame_ids"], list(range(1, 31)))
            self.assertLessEqual(result["sender_highwater_frames"], 2)

    def test_eager_preemption_starves_overloaded_complete_stereo(self):
        result = simulate("eager_preempt", pair_bytes=65536, rate_mbps=20, frames=30, ttl_ms=50)
        self.assertEqual(result["published_frame_ids"], [30])
        self.assertGreater(result["sender_counts"]["preempted_incomplete_frames"], 20)
        preserve = simulate("latest_pending", pair_bytes=65536, rate_mbps=20, frames=30, ttl_ms=50)
        self.assertGreater(preserve["complete_pairs"], 5)
        self.assertLessEqual(preserve["simulated_source_age_ms"]["max"], 50)

    def test_latest_pending_is_fresher_than_fifo_at_same_service(self):
        a = simulate("fifo_two", pair_bytes=65536, rate_mbps=20, frames=90, ttl_ms=100)
        b = simulate("latest_pending", pair_bytes=65536, rate_mbps=20, frames=90, ttl_ms=100)
        self.assertLess(b["simulated_source_age_ms"]["p95"], a["simulated_source_age_ms"]["p95"])
        self.assertGreater(b["sender_counts"]["superseded_pending_frames"], 0)

    def test_impossible_deadline_not_repaired_by_queue_policy(self):
        for policy in POLICIES:
            result = simulate(policy, pair_bytes=262144, rate_mbps=20, frames=30, ttl_ms=12)
            self.assertEqual(result["complete_pairs"], 0)

    def test_parity_adds_service_cost_on_lossless_link(self):
        a = simulate("latest_pending", pair_bytes=32768, rate_mbps=100, frames=10, stripe_width=0)
        b = simulate("latest_pending", pair_bytes=32768, rate_mbps=100, frames=10, stripe_width=8)
        self.assertEqual(a["complete_pairs"], b["complete_pairs"])
        self.assertGreater(b["assumed_ipv4_bytes_sent"], a["assumed_ipv4_bytes_sent"])
        self.assertGreater(b["simulated_source_age_ms"]["p50"], a["simulated_source_age_ms"]["p50"])

    def test_invalid_configuration(self):
        for args in ({"pair_bytes": 2}, {"rate_mbps": 0}, {"ttl_ms": 101}, {"frames": 1001}, {"stripe_width": 3}, {"policy": "unbounded"}):
            with self.assertRaises(ValueError):
                simulate(**args)

    def test_sender_expiry_and_replacement_do_not_mutate_flight(self):
        sender = Sender("eager_preempt")
        sender.offer(Frame(1, 100, [b"old1", b"old2"]))
        flight = sender.next_packet(0)
        sender.offer(Frame(2, 100, [b"new1"]))
        sender.completed_packet(1)
        self.assertEqual(flight, (1, b"old1"))
        self.assertEqual(sender.next_packet(1), (2, b"new1"))
        sender.completed_packet(2)
        self.assertIsNone(sender.next_packet(2))
        sender.offer(Frame(3, 3, [b"expired"]))
        self.assertIsNone(sender.next_packet(3))

    def test_sender_rejects_bad_bounds_and_regressing_identity(self):
        for frame in (Frame(0, 10, [b"x"]), Frame(1, 0, [b"x"]), Frame(1, 10, []),
                      Frame(1, 10, [b"x" * 1201]), Frame(1, 10, [b"x"] * 288)):
            with self.assertRaises(ValueError):
                Sender("latest_pending").offer(frame)
        sender = Sender("latest_pending")
        sender.offer(Frame(2, 100, [b"new"]))
        with self.assertRaises(ValueError):
            sender.offer(Frame(1, 100, [b"old"]))
        self.assertEqual(sender.next_packet(0), (2, b"new"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
